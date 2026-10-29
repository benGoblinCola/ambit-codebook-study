# Convenience targets.
#
# NOTE: `make` is not installed on Windows by default. Either install it
#   (winget install ezwinports.make   — or   choco install make), or on Windows
#   use the equivalents directly:  demo.cmd  /  the python scripts below.
.PHONY: ambit-demo test eval teardown

ambit-demo:   ## Start the gated studio + tunnel and print the public link
	powershell -ExecutionPolicy Bypass -File scripts/demo.ps1

test:         ## Run the unit tests
	python -m pytest tests -q

eval:         ## Run the eval suite (release gate)
	python evals/eval_suite.py

teardown:     ## Destroy the AWS stack + delete the app secret
	python scripts/teardown.py
