.PHONY: run validate clean

run:
	python run_pipeline.py

validate:
	python validate.py

clean:
	python -c "from run_pipeline import _reset_generated; _reset_generated()"
