.PHONY: dev dev-cheap dev-think dev-mid dev-pro copilot mvp prewarm report test test-hard help

.DEFAULT_GOAL := help

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*##"}{printf "  \033[36m%-12s\033[0m %s\n",$$1,$$2}'

# Primary “Copilot replacement” entry: same as mvp; tier = COPILOT_TIER or TIER or 2
copilot: ## Token-aware coding session (default cheap tier). Example: make copilot COPILOT_TIER=4c
	python3 scripts/mvp_aider.py --tier "$(or $(COPILOT_TIER),$(TIER),2)" $(MVP_EXTRA)

mvp: ## Same as copilot (tiered Aider). Example: make mvp TIER=4c
	python3 scripts/mvp_aider.py --tier "$(or $(TIER),2)" $(MVP_EXTRA)

dev: ## Claude Sonnet from .aider.conf.yml + prompt-cache prewarm (no --model override)
	python3 scripts/prewarm_cache.py
	aider

dev-cheap: ## DeepSeek-Chat only (no Claude prewarm — avoids ANTHROPIC_API_KEY requirement)
	aider --model deepseek/deepseek-chat

dev-think: ## DeepSeek-Reasoner only (no Claude prewarm)
	aider --model deepseek/deepseek-reasoner

dev-mid: ## Gemini 2.5 Flash (no Claude prewarm)
	aider --model gemini/gemini-2.5-flash

dev-pro: ## Gemini 2.5 Pro (no Claude prewarm)
	aider --model gemini/gemini-2.5-pro

prewarm: ## Pre-warm Claude prompt cache (run at session start)
	python3 scripts/prewarm_cache.py

report: ## Show token usage and cost for this session
	python3 scripts/token_report.py --verbose

test: ## Run full test suite
	python3 -m pytest tests/ -v --tb=short

test-hard: ## Run adversarial + full test suite with random order and fail-fast
	python3 -m pytest tests/ -v --tb=short --random-order -x --durations=10
