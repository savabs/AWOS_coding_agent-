# Math Protocol

> **Math is the product. LLM is the scaffold.**
> Once work moves into scoring, estimation, inference, filtering, optimization,
> or statistical control — this protocol applies.

---

## When This Protocol Activates

This protocol applies whenever implementation involves:
- Computing a score, rank, or signal from data
- Estimating a quantity with uncertainty
- Bayesian inference or belief updates
- Kalman filtering, particle filtering, or signal fusion
- Statistical hypothesis testing
- Optimization (convex, stochastic, combinatorial)
- Machine learning training or evaluation
- Probabilistic modeling (HMMs, BNs, VAEs, etc.)
- Information-theoretic quantities (entropy, mutual information, transfer entropy)
- Time series analysis (changepoints, regimes, spectral methods)
- Reinforcement learning (rewards, policies, value functions)

---

## Mandatory Explanation Before Coding

Before writing any math code, document the following in the spec or research note:

### 1. Quantity Definition
What is being computed? State it precisely.
```
Example:
  "Estimating the posterior distribution P(regime | observations_1..t)
   where regime ∈ {bull, bear, sideways} using a Hidden Markov Model."
```

### 2. Objective / Test Statistic
What is being optimized or tested?
```
Example:
  "Maximizing log-likelihood: L(θ) = Σ_t log P(o_t | regime_t, θ)
   using the Baum-Welch EM algorithm."
```

### 3. Null and Alternative (for hypothesis tests)
```
Example:
  "H₀: returns follow random walk (Hurst exponent H = 0.5)
   H₁: returns exhibit long-range dependence (H ≠ 0.5)
   Test: rescaled range analysis (R/S), significance level α = 0.05"
```

### 4. Assumptions
What must be true for this method to be valid?
```
Example:
  "HMM assumes: emissions are conditionally independent given state,
   state transitions are first-order Markov, number of regimes is fixed.
   Violation: non-stationarity of transition probabilities.
   Mitigation: rolling window refit every 90 days."
```

### 5. Numerical Stability Concerns
What can go wrong numerically?
```
Example:
  "Forward-backward algorithm suffers from underflow in long sequences.
   Solution: compute in log-space using log-sum-exp trick."
```

---

## Present Options Before Locking In

For any substantive mathematical implementation, compare at least two options:

| Option | Properties | When it fails | This project's preference |
|---|---|---|---|
| Exact EM (Baum-Welch) | Globally optimal for this likelihood; O(T·K²) | Large K, long sequences | Preferred for K≤10 |
| Stochastic EM | O(T·K) per step; approximate | May not converge with small batches | When T > 10K |
| Viterbi decoding | Most likely sequence; O(T·K²) | Doesn't give full posterior | Use for sequence labeling only |

State explicitly which option was chosen for this project and why.

---

## Anchor to Trusted Sources

Before applying any non-trivial mathematical method:

1. **Identify the trusted source** — primary paper, textbook chapter, or authoritative library docs
2. **Explain why this source is trustworthy** — peer-reviewed, widely cited, or directly from library maintainers
3. **Distinguish theory from engineering** — what is source-backed math vs. project-specific implementation choice

```markdown
## Mathematical Sources

| Method | Source | Why Trusted | Project Engineering Choices |
|---|---|---|---|
| HMM Baum-Welch | Rabiner 1989, "A Tutorial on HMMs" | Foundational paper, 30K citations | Using hmmlearn v0.3 which implements this exactly |
| EWC regularization | Kirkpatrick et al. 2017 (NeurIPS) | Original EWC paper, peer-reviewed | λ=5000 chosen empirically on this dataset |
| Kalman filter | Welch & Bishop 2006 tutorial | Standard reference, widely validated | Identity transition matrix (no-drift assumption) |
```

---

## Learnable vs Hand-Coded

**Hand-code:**
- Schemas and data structures
- Invariants and safety constraints
- Explicit factual relationships stated in source data
- System boundaries and input validation

**Learn:**
- Weights, scores, and ranking functions
- Ambiguous relationships between entities
- Predictive behavior (what happens next)
- Latent structure (hidden states, embeddings)
- Any relationship whose ground truth is probabilistic

If you find yourself hard-coding a weighting scheme like `score = 0.7 * x + 0.3 * y` — stop.
Ask: "Could a model learn this weighting from data?" If yes, prefer the learned version.

---

## Outputs Are Distributions, Not Point Estimates

The system produces probability distributions with uncertainty bounds, never point estimates or text opinions.

Good: `P(price_up_5pct in 30d) = 0.67 ± 0.08 [CI: 0.58–0.76]`
Bad: `price will go up`
Bad: `score: 7.3/10`

Every quantitative output must carry:
- A probability or probability distribution
- A confidence interval or uncertainty estimate
- The number of observations or data points the estimate is based on
- The time window or scope of the estimate

---

## Model Agnosticism Doctrine

No model is sacred. The current stack is the best-justified choice right now given data volume and scale.

If real backtests show a component is not producing genuine predictive edge — measured by out-of-sample calibration, Sharpe attribution, or convergence signal quality — that component must be replaced or upgraded.

**Replacement triggers:**
- Out-of-sample performance significantly below in-sample: overfitting — simplify or regularize
- Calibration curves show systematic bias: model form mismatch — change likelihood
- Convergence not improving with more data: model is too small or wrong architecture
- Runtime exceeding budget: approximate method or smaller model

**Not valid replacement triggers:**
- "I read about a newer method" — not sufficient; must show improvement on this data
- "The architecture is more elegant" — elegance is not edge
- "It works in the paper" — must work on this specific data distribution

Sunk cost is not a reason to keep a weak model. The test is always: does this produce real edge on real data?
