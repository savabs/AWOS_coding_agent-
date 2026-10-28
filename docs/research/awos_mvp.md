---
title: "Research: AWOS terminal MVP (Diramide coprocessor)"
tags:
  - doc/research
  - topic/awos
  - topic/context-optimization
  - status/active
---

# Research: AWOS terminal MVP

## Problem

General AI coding tools flatten repositories into enormous prompts. Cost and quality suffer from **context entropy**: models reason over irrelevant implementation text. Subscription tools (e.g. Copilot Pro+) scale cost poorly for sustained engineering.

## Hypothesis

A **terminal-native** layer that (1) **compresses** repo context into semantic skeletons, (2) **hydrates** full files only when needed, (3) **loops** edit–test–repair with structured failure signals, and (4) **remembers** project rationale in durable files will deliver **higher reasoning per token** than raw full-file context, at lower API cost.

## Constraints (MVP)

- Python + Typer CLI; few commands only.
- Persistent state under `.awos/`; no AGI, RL, or multi-agent cognition in v0.
- Git: autonomous work on isolated branches.
- Model routing: cheap default, strong models only when justified.

## Related

- [[awos_mvp_spec]] — implementation spec and phased plan
- [[AWOS]] — doctrine (`AWOS.md`)
- [[token_efficiency]] — prior token-efficiency research (Aider + Claude)
