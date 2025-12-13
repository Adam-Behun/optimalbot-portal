Run from `portal/`:

python evals/demo_clinic_alpha/patient_scheduling/run.py --list # List scenarios
python evals/demo_clinic_alpha/patient_scheduling/run.py --scenario <id>
python evals/demo_clinic_alpha/patient_scheduling/run.py --all # Run all
python evals/demo_clinic_alpha/patient_scheduling/run.py --sync-dataset # Sync to Langfuse

Results: `evals/demo_clinic_alpha/patient_scheduling/results/<scenario_id>/*.json` + Langfuse traces
