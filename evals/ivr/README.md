IVR Navigation Evaluation

# TEXT-BASED TESTING

python evals/ivr/run.py --quick-start

python evals/ivr/run.py --generate --multi 10 --dead-end 3

python evals/ivr/run.py --run --grade --test-file evals/ivr/test_cases/ivr_navigation.json

python evals/ivr/viewer.py --latest

python evals/ivr/run.py --generate --append --multi 5

# INTEGRATION TESTING

python evals/ivr/test_server.py

python evals/ivr/integration_test.py