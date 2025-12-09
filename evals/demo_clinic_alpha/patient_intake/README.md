# Patient Intake Evaluation

Run from `portal/`:

```bash
python evals/demo_clinic_alpha/patient_intake/run.py --list              # List scenarios
python evals/demo_clinic_alpha/patient_intake/run.py --scenario <id>     # Run one
python evals/demo_clinic_alpha/patient_intake/run.py --all               # Run all
python evals/demo_clinic_alpha/patient_intake/run.py --sync-dataset      # Sync to Langfuse
```

Results: `evals/demo_clinic_alpha/patient_intake/results/<scenario_id>/*.json` + Langfuse traces
