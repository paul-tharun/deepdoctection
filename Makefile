.PHONY: clean clean-test clean-pyc clean-build develop help venv start pytest test

UNAME_S := $(shell uname -s)


PYTHON=python3


analyze:
	mypy -p deepdoctection -p tests -p tests_d2

check-format:
	black --line-length 120 --check deepdoctection tests setup.py
	isort --check tests setup.py

clean: clean-build clean-pyc clean-test

clean-build:
	rm -fr build/
	rm -fr dist/
	rm -fr .eggs/
	find . -name '*.egg-info' -exec rm -fr {} +
	find . -name '*.egg' -exec rm -f {} +

clean-pyc:
	find . -name '*.pyc' -exec rm -f {} +
	find . -name '*.pyo' -exec rm -f {} +
	find . -name '*~' -exec rm -f {} +
	find . -name '__pycache__' -exec rm -fr {} +

clean-test:
	rm -fr .tox/
	rm -f .coverage
	rm -fr htmlcov/
	rm -fr .pytest_cache

format-and-qa: format qa

format:
	black --line-length 120 deepdoctection tests setup.py
	isort  deepdoctection tests setup.py

install-dd-dev-pt: check-venv
	@echo "--> Installing source-all-pt"
	pip install -e ".[source-all-pt]"
	@echo "--> Installing dev, test dependencies"
	pip install -e ".[dev, test]"
	@echo "--> Done installing dev, test dependencies"
	@echo ""

install-dd-dev-tf: check-venv
	@echo "--> Installing source-all-tf"
	pip install -e ".[source-all-tf]"
	@echo "--> Installing dev, test dependencies"
	pip install -e ".[dev, test]"
	@echo "--> Done installing dev, test dependencies"
	@echo ""

install-dd-test: check-venv
	@echo "--> Installing test dependencies"
	pip install -e ".[test]"
	@echo "--> Done installing test dependencies"
	@echo ""

install-jupyterlab-setup: check-venv
	@echo "--> Installing Jupyter Lab"
	pip install jupyterlab>=3.0.0
	@echo "--> Done installing Jupyter Lab"

install-kernel-dd: check-venv
	@echo "--> Installing IPkernel setup and setup kernel deep-doctection"
	pip install --user ipykernel
	$(PYTHON) -m ipykernel install --user --name=deep-doc
	@echo "--> Done installing kernel deep-doc"

install-kernel-dd-mac: check-venv
	@echo "--> Installing IPkernel setup and setup kernel deep-doctection"
	pip install ipykernel
	$(PYTHON) -m ipykernel install --name=deep-doc
	@echo "--> Done installing kernel deep-doc"

install-prodigy-setup: check-venv install-jupyterlab-setup
	@echo "--> Installing Jupyter Lab Prodigy plugin"
	pip install jupyterlab-prodigy
	jupyter labextension list
	@echo "--> Done installing Jupyter Lab Prodigy plugin"
	@echo ""

lint:
	pylint deepdoctection tests tests_d2

package: check-venv
	@echo "--> Generating package"
	pip install --upgrade build
	$(PYTHON) -m build

qa: lint analyze test

# all tests - this will never succeed in full due to dependency conflicts
test:
	pytest --cov=deepdoctection --cov-branch --cov-report=html tests
	pytest --cov=deepdoctection --cov-branch --cov-report=html tests_d2

test-build:
	pip install --upgrade build
	$(PYTHON) -m build
	pip install --upgrade twine
	$(PYTHON) -m twine upload --repository testpypi dist/*

test-tf-basic:
	pytest --cov=deepdoctection --cov-branch --cov-report=html -m "not requires_pt and not full and not all" tests

test-tf-full:
	pytest --cov=deepdoctection --cov-branch --cov-report=html -m "not requires_pt and not all" tests

test-tf-all:
	pytest --cov=deepdoctection --cov-branch --cov-report=html -m "not requires_pt" tests

test-pt-full: test-integration
	pytest --cov=deepdoctection --cov-branch --cov-report=html -m "not requires_tf and not all" tests
	pytest --cov=deepdoctection --cov-branch --cov-report=html tests_d2

test-pt-all: test-integration
	pytest --cov=deepdoctection --cov-branch --cov-report=html -m "not requires_tf" tests
	pytest --cov=deepdoctection --cov-branch --cov-report=html tests_d2

test-integration:
	pytest --cov=deepdoctection --cov-branch --cov-report=html -m "integration" tests

up-pip: check-venv
	@echo "--> Updating pip"
	pip install pip
	pip install --upgrade pip pip-tools
	pip install wheel
	@echo "--> Done updating pip"

up-req: check-venv
	@echo "--> Updating Python requirements"
	pip-compile  --output-file requirements.txt  setup.py
	@echo "--> Done updating Python requirements"

up-req-docs: check-venv
	@echo "--> Updating Python requirements"
	pip-compile  --output-file docs/requirements.txt  --extra docs  setup.py
	@echo "--> Done updating Python requirements"



venv:
	$(PYTHON) -m venv venv --system-site-packages

check-venv:
ifndef VIRTUAL_ENV
	$(error Please activate virtualenv first)
endif