"""Pacote do Lab 04.2 — SLM no SageMaker + entrega contínua segura.

Layout espelha o 04.1 (`fiap_ml_operations`): um módulo de fronteira com a AWS
(`aws.py`), um módulo de config lido de `config/lab.yaml`, e um módulo de
evidência. Outros agentes (A5-A10) adicionam `hf_release.py`, `inference.py`
e `evaluation.py` neste mesmo pacote — nenhum deles reimplementa `aws.py`.
"""
