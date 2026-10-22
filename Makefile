PY=.venv/bin/python
CRAG=.venv/bin/crag

setup:            ## create venv + install (needs python3.12 and tesseract)
	python3.12 -m venv .venv && .venv/bin/pip install -U pip && .venv/bin/pip install -e ".[dev]"

ingest:           ## OCR + parse + chunk all sources (cached OCR is reused)
	$(CRAG) ingest

index:            ## embed + build hybrid Qdrant index
	$(CRAG) index

review:           ## regenerate review CSVs in data/processed/
	$(CRAG) export-review

verify-tables:    ## cross-check curated code tables against OCR text
	$(CRAG) verify-tables

test:
	$(PY) -m pytest -q

eval:
	$(CRAG) evaluate

serve:
	$(CRAG) serve --host 0.0.0.0 --port 8000

up:               ## optional: Qdrant server + Postgres + API in Docker
	docker compose up -d --build
