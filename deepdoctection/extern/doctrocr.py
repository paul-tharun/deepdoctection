# -*- coding: utf-8 -*-
# File: doctrocr.py

# Copyright 2021 Dr. Janis Meyer. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Deepdoctection wrappers for DocTr OCR text line detection and text recognition models
"""
import os
from abc import ABC
from pathlib import Path
from typing import Any, List, Literal, Mapping, Optional, Tuple
from zipfile import ZipFile

from ..utils.detection_types import ImageType, Requirement
from ..utils.env_info import get_device
from ..utils.error import DependencyError
from ..utils.file_utils import (
    doctr_available,
    get_doctr_requirement,
    get_pytorch_requirement,
    get_tensorflow_requirement,
    get_tf_addons_requirements,
    pytorch_available,
    tf_addons_available,
    tf_available,
)
from ..utils.fs import load_json
from ..utils.settings import LayoutType, ObjectTypes, PageType, TypeOrStr
from ..utils.viz import viz_handler
from .base import DetectionResult, ImageTransformer, ObjectDetector, PredictorBase, TextRecognizer
from .pt.ptutils import set_torch_auto_device

if doctr_available() and ((tf_addons_available() and tf_available()) or pytorch_available()):
    from doctr.models._utils import estimate_orientation
    from doctr.models.detection.predictor import DetectionPredictor  # pylint: disable=W0611
    from doctr.models.detection.zoo import detection_predictor
    from doctr.models.preprocessor import PreProcessor
    from doctr.models.recognition.predictor import RecognitionPredictor  # pylint: disable=W0611
    from doctr.models.recognition.zoo import ARCHS, recognition

if pytorch_available():
    import torch

if tf_available():
    import tensorflow as tf  # type: ignore  # pylint: disable=E0401


def _set_device_str(device: Optional[str] = None) -> str:
    if device is not None:
        if tf_available():
            device = "/" + device.replace("cuda", "gpu") + ":0"
    elif pytorch_available():
        device = set_torch_auto_device()
    else:
        device = "/gpu:0"  # we impose to install tensorflow-gpu because of Tensorpack models
    return device


def _load_model(path_weights: str, doctr_predictor: Any, device: str, lib: Literal["PT", "TF"]) -> None:
    if lib == "PT" and pytorch_available():
        state_dict = torch.load(path_weights, map_location=device)
        for key in list(state_dict.keys()):
            state_dict["model." + key] = state_dict.pop(key)
        doctr_predictor.load_state_dict(state_dict)
        doctr_predictor.to(device)
    elif lib == "TF" and tf_available():
        # Unzip the archive
        params_path = Path(path_weights).parent
        is_zip_path = path_weights.endswith(".zip")
        if is_zip_path:
            with ZipFile(path_weights, "r") as file:
                file.extractall(path=params_path)
                doctr_predictor.model.load_weights(params_path / "weights")
        else:
            doctr_predictor.model.load_weights(path_weights)


def auto_select_lib_for_doctr() -> Literal["PT", "TF"]:
    """Auto select the DL library from the installed and from environment variables"""
    if tf_available() and os.environ.get("USE_TF", os.environ.get("USE_TENSORFLOW", False)):
        os.environ["USE_TF"] = "TRUE"
        return "TF"
    if pytorch_available() and os.environ.get("USE_TORCH", os.environ.get("USE_PYTORCH", False)):
        return "PT"
    raise DependencyError("Neither Tensorflow nor PyTorch has been installed. Cannot use DoctrTextlineDetector")


def doctr_predict_text_lines(np_img: ImageType, predictor: "DetectionPredictor", device: str) -> List[DetectionResult]:
    """
    Generating text line DetectionResult based on Doctr DetectionPredictor.

    :param np_img: Image in np.array.
    :param predictor: `doctr.models.detection.predictor.DetectionPredictor`
    :param device: Will only be used in tensorflow settings. Either /gpu:0 or /cpu:0
    :return: A list of text line detection results (without text).
    """
    if tf_available() and device is not None:
        with tf.device(device):
            raw_output = predictor([np_img])
    else:
        raw_output = predictor([np_img])
    detection_results = [
        DetectionResult(
            box=box[:4].tolist(), class_id=1, score=box[4], absolute_coords=False, class_name=LayoutType.word
        )
        for box in raw_output[0]["words"]
    ]
    return detection_results


def doctr_predict_text(
    inputs: List[Tuple[str, ImageType]], predictor: "RecognitionPredictor", device: str
) -> List[DetectionResult]:
    """
    Calls Doctr text recognition model on a batch of numpy arrays (text lines predicted from a text line detector) and
    returns the recognized text as DetectionResult

    :param inputs: list of tuples containing the annotation_id of the input image and the numpy array of the cropped
                   text line
    :param predictor: `doctr.models.detection.predictor.RecognitionPredictor`
    :param device: Will only be used in tensorflow settings. Either /gpu:0 or /cpu:0
    :return: A list of DetectionResult containing recognized text.
    """

    uuids, images = list(zip(*inputs))
    if tf_available() and device is not None:
        with tf.device(device):
            raw_output = predictor(list(images))
    else:
        raw_output = predictor(list(images))
    detection_results = [
        DetectionResult(score=output[1], text=output[0], uuid=uuid) for uuid, output in zip(uuids, raw_output)
    ]
    return detection_results


class DoctrTextlineDetectorMixin(ObjectDetector, ABC):
    """Base class for Doctr textline detector. This class only implements the basic wrapper functions"""

    def __init__(self, categories: Mapping[str, TypeOrStr], lib: Optional[Literal["PT", "TF"]] = None):
        self.categories = categories  # type: ignore
        self.lib = lib if lib is not None else self.auto_select_lib()

    def possible_categories(self) -> List[ObjectTypes]:
        return [LayoutType.word]

    @staticmethod
    def get_name(path_weights: str, architecture: str) -> str:
        """Returns the name of the model"""
        return f"doctr_{architecture}" + "_".join(Path(path_weights).parts[-2:])

    @staticmethod
    def auto_select_lib() -> Literal["PT", "TF"]:
        """Auto select the DL library from the installed and from environment variables"""
        return auto_select_lib_for_doctr()


class DoctrTextlineDetector(DoctrTextlineDetectorMixin):
    """
    A deepdoctection wrapper of DocTr text line detector. We model text line detection as ObjectDetector
    and assume to use this detector in a ImageLayoutService.
    DocTr supports several text line detection implementations but provides only a subset of pre-trained models.
    The most usable one for document OCR for which a pre-trained model exists is DBNet as described in “Real-time Scene
    Text Detection with Differentiable Binarization”, with a ResNet-50 backbone. This model can be used in either
    Tensorflow or PyTorch.
    Some other pre-trained models exist that have not been registered in `ModelCatalog`. Please check the DocTr library
    and organize the download of the pre-trained model by yourself.

    **Example:**

                 path_weights_tl = ModelDownloadManager.maybe_download_weights_and_configs("doctr/db_resnet50/pt
                 /db_resnet50-ac60cadc.pt")
                 # Use "doctr/db_resnet50/tf/db_resnet50-adcafc63.zip" for Tensorflow

                 categories = ModelCatalog.get_profile("doctr/db_resnet50/pt/db_resnet50-ac60cadc.pt").categories
                 det = DoctrTextlineDetector("db_resnet50",path_weights_tl,categories,"cpu")
                 layout = ImageLayoutService(det,to_image=True, crop_image=True)

                 path_weights_tr = dd.ModelDownloadManager.maybe_download_weights_and_configs("doctr/crnn_vgg16_bn
                 /pt/crnn_vgg16_bn-9762b0b0.pt")
                 rec = DoctrTextRecognizer("crnn_vgg16_bn", path_weights_tr, "cpu")
                 text = TextExtractionService(rec, extract_from_roi="word")

                 analyzer = DoctectionPipe(pipeline_component_list=[layout,text])

                 path = "/path/to/image_dir"
                 df = analyzer.analyze(path = path)

                 for dp in df:
                     ...
    """

    def __init__(
        self,
        architecture: str,
        path_weights: str,
        categories: Mapping[str, TypeOrStr],
        device: Optional[Literal["cpu", "cuda"]] = None,
        lib: Optional[Literal["PT", "TF"]] = None,
    ) -> None:
        """
        :param architecture: DocTR supports various text line detection models, e.g. "db_resnet50",
        "db_mobilenet_v3_large". The full list can be found here:
        https://github.com/mindee/doctr/blob/main/doctr/models/detection/zoo.py#L20
        :param path_weights: Path to the weights of the model
        :param categories: A dict with the model output label and value
        :param device: "cpu" or "cuda". Will default to "cuda" if the required hardware is available.
        :param lib: "TF" or "PT" or None. If None, env variables USE_TENSORFLOW, USE_PYTORCH will be used.
        """
        super().__init__(categories, lib)
        self.architecture = architecture
        self.path_weights = path_weights

        self.name = self.get_name(self.path_weights, self.architecture)
        self.model_id = self.get_model_id()

        if device is None:
            if self.lib == "TF":
                device = "cuda" if tf.test.is_gpu_available() else "cpu"
            elif self.lib == "PT":
                auto_device = get_device(False)
                device = "cpu" if auto_device == "mps" else auto_device
            else:
                raise DependencyError("Cannot select device automatically. Please set the device manually.")

        self.device_input = device
        self.device = _set_device_str(device)
        self.doctr_predictor = self.get_wrapped_model(self.architecture, self.path_weights, self.device_input, self.lib)

    def predict(self, np_img: ImageType) -> List[DetectionResult]:
        """
        Prediction per image.

        :param np_img: image as numpy array
        :return: A list of DetectionResult
        """
        detection_results = doctr_predict_text_lines(np_img, self.doctr_predictor, self.device)
        return detection_results

    @classmethod
    def get_requirements(cls) -> List[Requirement]:
        if tf_available():
            return [get_tensorflow_requirement(), get_doctr_requirement(), get_tf_addons_requirements()]
        if pytorch_available():
            return [get_pytorch_requirement(), get_doctr_requirement()]
        raise ModuleNotFoundError("Neither Tensorflow nor PyTorch has been installed. Cannot use DoctrTextlineDetector")

    def clone(self) -> PredictorBase:
        return self.__class__(self.architecture, self.path_weights, self.categories, self.device_input, self.lib)

    @staticmethod
    def load_model(path_weights: str, doctr_predictor: Any, device: str, lib: Literal["PT", "TF"]) -> None:
        """Loading model weights"""
        _load_model(path_weights, doctr_predictor, device, lib)

    @staticmethod
    def get_wrapped_model(
        architecture: str, path_weights: str, device: Literal["cpu", "cuda"], lib: Literal["PT", "TF"]
    ) -> Any:
        """
        Get the inner (wrapped) model.

        :param architecture: DocTR supports various text line detection models, e.g. "db_resnet50",
        "db_mobilenet_v3_large". The full list can be found here:
        https://github.com/mindee/doctr/blob/main/doctr/models/detection/zoo.py#L20
        :param path_weights: Path to the weights of the model
        :param device: "cpu" or "cuda". Will default to "cuda" if the required hardware is available.
        :param lib: "TF" or "PT" or None. If None, env variables USE_TENSORFLOW, USE_PYTORCH will be used. Make sure,
                    these variables are set. If not, use

                        deepdoctection.utils.env_info.auto_select_lib_and_device

        :return: Inner model which is a "nn.Module" in PyTorch or a "tf.keras.Model" in Tensorflow
        """
        doctr_predictor = detection_predictor(arch=architecture, pretrained=False, pretrained_backbone=False)
        device_str = _set_device_str(device)
        DoctrTextlineDetector.load_model(path_weights, doctr_predictor, device_str, lib)
        return doctr_predictor


class DoctrTextRecognizer(TextRecognizer):
    """
    A deepdoctection wrapper of DocTr text recognition predictor. The base class is a TextRecognizer that takes
    a batch of sub images (e.g. text lines from a text detector) and returns a list with text spotted in the sub images.
    DocTr supports several text recognition models but provides only a subset of pre-trained models.

    This model that is most suitable for document text recognition is the CRNN implementation with a VGG-16 backbone as
    described in “An End-to-End Trainable Neural Network for Image-based Sequence Recognition and Its Application to
    Scene Text Recognition”. It can be used in either Tensorflow or PyTorch.

    For more details please check the official DocTr documentation by Mindee: https://mindee.github.io/doctr/

    **Example:**

                 path_weights_tl = ModelDownloadManager.maybe_download_weights_and_configs("doctr/db_resnet50/pt
                 /db_resnet50-ac60cadc.pt")
                 # Use "doctr/db_resnet50/tf/db_resnet50-adcafc63.zip" for Tensorflow

                 categories = ModelCatalog.get_profile("doctr/db_resnet50/pt/db_resnet50-ac60cadc.pt").categories
                 det = DoctrTextlineDetector("db_resnet50",path_weights_tl,categories,"cpu")
                 layout = ImageLayoutService(det,to_image=True, crop_image=True)

                 path_weights_tr = dd.ModelDownloadManager.maybe_download_weights_and_configs("doctr/crnn_vgg16_bn
                 /pt/crnn_vgg16_bn-9762b0b0.pt")
                 rec = DoctrTextRecognizer("crnn_vgg16_bn", path_weights_tr, "cpu")
                 text = TextExtractionService(rec, extract_from_roi="word")

                 analyzer = DoctectionPipe(pipeline_component_list=[layout,text])

                 path = "/path/to/image_dir"
                 df = analyzer.analyze(path = path)

                 for dp in df:
                     ...

    """

    def __init__(
        self,
        architecture: str,
        path_weights: str,
        device: Optional[Literal["cpu", "cuda"]] = None,
        lib: Optional[Literal["PT", "TF"]] = None,
        path_config_json: Optional[str] = None,
    ) -> None:
        """
        :param architecture: DocTR supports various text recognition models, e.g. "crnn_vgg16_bn",
        "crnn_mobilenet_v3_small". The full list can be found here:
        https://github.com/mindee/doctr/blob/main/doctr/models/recognition/zoo.py#L16.
        :param path_weights: Path to the weights of the model
        :param device: "cpu" or "cuda". Will default to "cuda" if the required hardware is available.
        :param lib: "TF" or "PT" or None. If None, env variables USE_TENSORFLOW, USE_PYTORCH will be used.
        :param path_config_json: Path to a json file containing the configuration of the model. Useful, if you have
        a model trained on custom vocab.
        """

        self.lib = lib if lib is not None else self.auto_select_lib()

        self.architecture = architecture
        self.path_weights = path_weights

        self.name = self.get_name(self.path_weights, self.architecture)
        self.model_id = self.get_model_id()

        if device is None:
            if self.lib == "TF":
                device = "cuda" if tf.test.is_gpu_available() else "cpu"
            if self.lib == "PT":
                auto_device = get_device(False)
                device = "cpu" if auto_device == "mps" else auto_device
            else:
                raise DependencyError("Cannot select device automatically. Please set the device manually.")

        self.device_input = device
        self.device = _set_device_str(device)
        self.path_config_json = path_config_json
        self.doctr_predictor = self.build_model(self.architecture, self.path_config_json)
        self.load_model(self.path_weights, self.doctr_predictor, self.device, self.lib)
        self.doctr_predictor = self.get_wrapped_model(
            self.architecture, self.path_weights, self.device_input, self.lib, self.path_config_json
        )

    def predict(self, images: List[Tuple[str, ImageType]]) -> List[DetectionResult]:
        """
        Prediction on a batch of text lines

        :param images: list of tuples with the annotation_id of the sub image and a numpy array
        :return: A list of DetectionResult
        """
        if images:
            return doctr_predict_text(images, self.doctr_predictor, self.device)
        return []

    @classmethod
    def get_requirements(cls) -> List[Requirement]:
        if tf_available():
            return [get_tensorflow_requirement(), get_doctr_requirement(), get_tf_addons_requirements()]
        if pytorch_available():
            return [get_pytorch_requirement(), get_doctr_requirement()]
        raise ModuleNotFoundError("Neither Tensorflow nor PyTorch has been installed. Cannot use DoctrTextRecognizer")

    def clone(self) -> PredictorBase:
        return self.__class__(self.architecture, self.path_weights, self.device_input, self.lib)

    @staticmethod
    def load_model(path_weights: str, doctr_predictor: Any, device: str, lib: Literal["PT", "TF"]) -> None:
        """Loading model weights"""
        _load_model(path_weights, doctr_predictor, device, lib)

    @staticmethod
    def build_model(architecture: str, path_config_json: Optional[str] = None) -> "RecognitionPredictor":
        """Building the model"""

        # inspired and adapted from https://github.com/mindee/doctr/blob/main/doctr/models/recognition/zoo.py
        custom_configs = {}
        batch_size = 32
        recognition_configs = {}
        if path_config_json:
            custom_configs = load_json(path_config_json)
            custom_configs.pop("arch", None)
            custom_configs.pop("url", None)
            custom_configs.pop("task", None)
            recognition_configs["mean"] = custom_configs.pop("mean")
            recognition_configs["std"] = custom_configs.pop("std")
            batch_size = custom_configs.pop("batch_size")
        recognition_configs["batch_size"] = batch_size

        if isinstance(architecture, str):
            if architecture not in ARCHS:
                raise ValueError(f"unknown architecture '{architecture}'")

            model = recognition.__dict__[architecture](pretrained=True, pretrained_backbone=True, **custom_configs)
        else:
            if not isinstance(
                architecture,
                (recognition.CRNN, recognition.SAR, recognition.MASTER, recognition.ViTSTR, recognition.PARSeq),
            ):
                raise ValueError(f"unknown architecture: {type(architecture)}")
            model = architecture

        input_shape = model.cfg["input_shape"][:2] if tf_available() else model.cfg["input_shape"][-2:]
        return RecognitionPredictor(PreProcessor(input_shape, preserve_aspect_ratio=True, **recognition_configs), model)

    @staticmethod
    def get_wrapped_model(
        architecture: str,
        path_weights: str,
        device: Literal["cpu", "cuda"],
        lib: Literal["PT", "TF"],
        path_config_json: Optional[str] = None,
    ) -> Any:
        """
        Get the inner (wrapped) model.

        :param architecture: DocTR supports various text recognition models, e.g. "crnn_vgg16_bn",
        "crnn_mobilenet_v3_small". The full list can be found here:
        https://github.com/mindee/doctr/blob/main/doctr/models/recognition/zoo.py#L16.
        :param path_weights: Path to the weights of the model
        :param device: "cpu" or "cuda". Will default to "cuda" if the required hardware is available.
        :param lib: "TF" or "PT" or None. If None, env variables USE_TENSORFLOW, USE_PYTORCH will be used.
        :param path_config_json: Path to a json file containing the configuration of the model. Useful, if you have
        a model trained on custom vocab.
        :return: Inner model which is a "nn.Module" in PyTorch or a "tf.keras.Model" in Tensorflow
        """
        doctr_predictor = DoctrTextRecognizer.build_model(architecture, path_config_json)
        device_str = _set_device_str(device)
        DoctrTextRecognizer.load_model(path_weights, doctr_predictor, device_str, lib)
        return doctr_predictor

    @staticmethod
    def get_name(path_weights: str, architecture: str) -> str:
        """Returns the name of the model"""
        return f"doctr_{architecture}" + "_".join(Path(path_weights).parts[-2:])

    @staticmethod
    def auto_select_lib() -> Literal["PT", "TF"]:
        """Auto select the DL library from the installed and from environment variables"""
        return auto_select_lib_for_doctr()


class DocTrRotationTransformer(ImageTransformer):
    """
    The `DocTrRotationTransformer` class is a specialized image transformer that is designed to handle image rotation
    in the context of Optical Character Recognition (OCR) tasks. It inherits from the `ImageTransformer` base class and
    implements methods for predicting and applying rotation transformations to images.

    The `predict` method determines the angle of the rotated image using the `estimate_orientation` function from the
    `doctr.models._utils` module. The `n_ct` and `ratio_threshold_for_lines` parameters for this function can be
    configured when instantiating the class.

    The `transform` method applies the predicted rotation to the image, effectively rotating the image backwards.
    This method uses either the Pillow library or OpenCV for the rotation operation, depending on the configuration.

    This class can be particularly useful in OCR tasks where the orientation of the text in the image matters.
    The class also provides methods for cloning itself and for getting the requirements of the OCR system.

    **Example:**
                    transformer = DocTrRotationTransformer()
                    detection_result = transformer.predict(np_img)
                    rotated_image = transformer.transform(np_img, detection_result)
    """

    def __init__(self, number_contours: int = 50, ratio_threshold_for_lines: float = 5):
        """

        :param number_contours: the number of contours used for the orientation estimation
        :param ratio_threshold_for_lines: this is the ratio w/h used to discriminates lines
        """
        self.number_contours = number_contours
        self.ratio_threshold_for_lines = ratio_threshold_for_lines
        self.name = "doctr_rotation_transformer"

    def transform(self, np_img: ImageType, specification: DetectionResult) -> ImageType:
        """
        Applies the predicted rotation to the image, effectively rotating the image backwards.
        This method uses either the Pillow library or OpenCV for the rotation operation, depending on the configuration.

        :param np_img: The input image as a numpy array.
        :param specification: A `DetectionResult` object containing the predicted rotation angle.
        :return: The rotated image as a numpy array.
        """
        return viz_handler.rotate_image(np_img, specification.angle)  # type: ignore

    def predict(self, np_img: ImageType) -> DetectionResult:
        angle = estimate_orientation(np_img, self.number_contours, self.ratio_threshold_for_lines)
        if angle < 0:
            angle += 360
        return DetectionResult(angle=round(angle, 2))

    @classmethod
    def get_requirements(cls) -> List[Requirement]:
        return [get_doctr_requirement()]

    def clone(self) -> PredictorBase:
        return self.__class__(self.number_contours, self.ratio_threshold_for_lines)

    @staticmethod
    def possible_category() -> PageType:
        return PageType.angle
