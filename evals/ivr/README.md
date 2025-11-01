IVR Navigation Evaluation

export OPENAI_API_KEY=your-key
export ANTHROPIC_API_KEY=your-key
python3 evals/ivr/run.py --quick-start

UNDERSTANDING TEST CASE COUNTS

GENERATE TEST CASES

Append new cases to existing
python evals/ivr/run.py --generate --append --single 2 --multi 2 --dead-end 2

RUN AND GRADE

python evals/ivr/run.py --run --grade --test-file evals/ivr/test_cases/ivr_navigation.json

# Workflow
Initial setup (generate test cases once):

python evals/ivr/run.py --generate --single 2 --multi 2 --dead-end 2

When you change prompts and want to re-test:

# Run tests against existing test cases
python evals/ivr/run.py --run --grade --test-file evals/ivr/test_cases/ivr_navigation.json

# View latest results
python evals/ivr/results_viewer.py --latest

Complete workflow summary:

# ONE TIME: Generate test cases
python3 evals/ivr/run.py --generate --single 10 --multi 5 --dead-end 3

# EVERY TIME you change the prompt:
python3 evals/ivr/run.py --run --grade --test-file evals/ivr/test_cases/ivr_navigation.json
python3 evals/ivr/results_viewer.py --latest

# Add more test cases later (keeps existing ones):
python3 evals/ivr/run.py --generate --append --single 5

The key is: generate once, run many times. The test cases in evals/ivr/test_cases/ivr_navigation.json stay the same, so you
can iterate on your prompt and see if more tests pass.