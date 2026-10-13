# make check：一条命令跑完全部离线验证（不需要 GPU，也不需要集群）
PY := .venv/bin/python
YAMLS := $(shell find lessons -name '*.yaml')

.PHONY: check
check:
	$(PY) -m pytest -q
	$(PY) scripts/check_pins.py $(YAMLS)
	kubeconform -strict -summary -kubernetes-version 1.35.0 -ignore-filename-pattern 'rules|dashboards|registries' lessons
	shellcheck scripts/*.sh
	promtool check rules lessons/03-monitoring/rules/vllm-alerts.yaml
	promtool test rules lessons/03-monitoring/rules/vllm-alerts.test.yaml
