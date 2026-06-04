.PHONY: help install test lint clean download batch all

PYTHON ?= python3
NODE ?= node

help:  ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

install:  ## Install Python and Node dependencies
	pip install -r requirements.txt
	npm install
	npx playwright install chromium

test:  ## Run unit tests
	$(PYTHON) -m pytest tests/ -v

lint:  ## Quick syntax check
	$(PYTHON) -m py_compile src/py4kvm/*.py scripts/*.py examples/*.py
	$(NODE) -c scripts/extract_m3u8.js
	$(NODE) -c scripts/find_episodes.js

clean:  ## Remove work directories, caches, downloaded files
	rm -rf video_download/ batch_work/ downloads/
	rm -rf __pycache__ */__pycache__ */*/__pycache__
	rm -rf .pytest_cache/ .mypy_cache/
	find . -name "*.pyc" -delete

download:  ## Download a single video: make download URL=https://...
	$(PYTHON) scripts/download.py $(URL)

batch:  ## Download a full season: make batch URL=https://...
	$(PYTHON) scripts/batch_download.py $(URL)
