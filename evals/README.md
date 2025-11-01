# Evaluation Framework

Standalone testing system for evaluating prompt performance without affecting production code.

## What It Does

Tests responses in isolation:
- Renders prompts with patient data
- Calls OpenAI API
- Grades responses using Claude as model-based grader
- Measures latency

Does NOT test voice pipeline, telephony, or production services.

## Evaluation Types

IVR Navigation evals/ivr
Tests IVR menu navigation decision-making.
python3 evals/ivr/run.py --quick-start --num-cases 5

LLM Conversational evals/llm
Tests conversational response quality.
python3 evals/llm/run.py --quick-start --state greeting --num-cases 5

## Available States

IVR
ivr_navigation - Phone menu navigation decisions

LLM
greeting - Initial greeting to human representative
verification - Patient information provision

## Metrics

IVR Navigation
Navigation decision accuracy
Latency

LLM Conversational
Information correctness
Conversational quality
Task completion
Latency

## Structure

evals/
├── README.md
├── ivr/
│   ├── framework.py
│   ├── test_generator.py
│   ├── graders.py
│   ├── run.py
│   ├── test_cases/
│   └── results/
├── llm/
│   ├── framework.py
│   ├── test_generator.py
│   ├── graders.py
│   ├── run.py
│   ├── test_cases/
│   └── results/
├── stt/
├── tts/
└── e2e/