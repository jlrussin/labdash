---
name: analysis
description: >
  Use this skill whenever doing data analysis, building visualizations, running statistical tests,
  or exploring experimental data. Triggers include: "analyze the data," "make a plot," "run a
  statistical test," "visualize," "what do the results show," "compare conditions," "check for
  effects," working with DataFrames, building figures with matplotlib/seaborn, computing summary
  statistics, fitting models, or any conversation about interpreting experimental results.
  This skill focuses on scientific rigor, human oversight, and the division of responsibilities
  between scientist and AI agent. It applies regardless of tooling (notebooks, scripts, LabDash).
---

# Scientific Analysis Skill

This skill governs how AI agents assist with scientific data analysis. The core principle: **the scientist owns the science, the agent owns the code.** Every analysis decision with scientific implications must be made or approved by the human. The agent's job is to implement faithfully, verify thoroughly, and surface anything unexpected.

Read `references/statistical-practices.md` for detailed guidance on specific statistical methods. Read `references/visualization-principles.md` for plotting standards.

## 1. Responsibilities

### The scientist (human) decides:
- What questions to ask of the data
- Which analyses to run and why
- How to interpret results
- Whether an unexpected finding is meaningful or an artifact
- When results are ready for publication
- Exclusion criteria and how to handle edge cases

### The agent implements:
- Writing correct, readable analysis code
- Running analyses and reporting results accurately
- Flagging anomalies, warnings, or unexpected patterns
- Verifying that code does what it claims to do
- Suggesting visualizations that would help the scientist evaluate results
- Documenting methodology clearly enough for a Methods section

### The boundary:
When in doubt about whether something is a scientific decision or an implementation decision, **ask**. Examples of things that look like implementation but are actually science:
- Choosing between mean and median (depends on distribution assumptions)
- Deciding how to handle missing data (listwise deletion vs imputation changes conclusions)
- Picking bin widths for histograms (can hide or reveal structure)
- Selecting which variables to control for (changes the story)
- Choosing a significance threshold (convention vs context-dependent)

## 2. Visualization-First Philosophy

**Every analysis should produce a visualization.** Tables of numbers are harder for humans to audit than plots. A scientist looking at a plot can immediately spot whether results make sense; a table of p-values requires trusting the pipeline.

Concrete rules:
- Before reporting any statistical test, plot the raw data the test operates on
- Show distributions, not just means — box plots, violin plots, or jittered points
- When comparing groups, show individual data points overlaid on summary statistics
- For regression or correlation, always show the scatter plot with the fitted line
- Label axes clearly with units. Include sample sizes in titles or legends
- **Never aggregate across between-subjects factors** without explicit scientist approval — always split by condition first

When the scientist asks for "a quick analysis," the minimum output is:
1. A plot showing the data
2. A one-sentence description of what the plot shows
3. The key number(s) — effect size, test statistic, p-value — presented alongside the plot, not instead of it

## 3. Statistical Rigor

See `references/statistical-practices.md` for detailed guidance. Key principles:

**Report effect sizes alongside test statistics.** A p-value without an effect size is incomplete. Always report: the effect size measure appropriate to the test (Cohen's d, eta-squared, r, odds ratio), the confidence interval, and the p-value.

**Check assumptions before running tests.** Before a t-test, check normality and variance homogeneity. Before ANOVA, check sphericity. Before correlation, check linearity. Before regression, check residuals. If assumptions are violated, flag it and suggest alternatives — don't silently proceed.

**Handle multiple comparisons.** When running more than one test on the same data, apply appropriate corrections (Bonferroni, Holm, FDR) or use omnibus tests first. Flag when multiple comparisons are being made even if the scientist didn't ask for correction — let them decide.

**Be transparent about sample sizes.** Always report N. When N is small, say so explicitly and note the implications for power. Don't present results from N=3 with the same confidence framing as N=300.

**Distinguish exploratory from confirmatory analysis.** If the scientist is exploring the data (no pre-registered hypothesis), label the analysis as exploratory. If following a pre-registered plan, note any deviations. This distinction matters for how results should be interpreted and reported.

## 4. Reproducibility

**Deterministic output.** All analysis code must produce identical results given identical input data. If randomness is involved (bootstrap CIs, permutation tests), use and document a random seed.

**Self-contained code.** Each analysis script should be runnable independently. Document all dependencies. The scientist should be able to re-run any analysis months later and get the same result.

**Data provenance.** At the top of every analysis, document where the data came from, what filtering was applied, and what the final sample size is. Print these facts so they're visible in the output.

## 5. Incremental Verification

**Show intermediate results.** Don't chain 10 analyses and present only the final result. After each major step (data loading, filtering, transformation, analysis), present a checkpoint:
- Data loading: print shape, column names, sample rows
- Filtering: print how many rows were removed and why
- Transformation: show before/after for a few rows
- Analysis: show the plot before moving to the next analysis

**Ask before building on unexpected results.** If an intermediate result is surprising (e.g., accuracy below chance, impossible RT values, empty groups), stop and report it. Don't assume it's fine and proceed.

**Verify your own code.** After writing analysis code, do a quick sanity check:
- Does the sample size in the output match what you expect?
- Are the column names what you intended?
- Does a simple manual calculation on a subset match the code's output?
- Are the axis labels and legends correct?

## 6. Communication

**Lead with the finding, not the method.** When presenting results: "Accuracy increases with ordinal distance (r = .82, p < .001)" not "We ran a Pearson correlation on accuracy and ordinal distance."

**Use precise language.** Say "participants in the skewed condition were faster" not "the skewed condition was faster." Say "the difference was not statistically significant" not "there was no difference."

**Flag uncertainty.** If results are ambiguous, say so. If the sample is too small for the test to be meaningful, say so. Don't launder uncertainty through confident-sounding statistical language.

**Present alternative interpretations.** When a result could be explained multiple ways, mention the alternatives. The scientist decides which interpretation is most plausible.

## 7. Code Conventions for Analysis Scripts

Whether using LabDash, standalone scripts, or any other tooling:

- **Aesthetic variables at the top** of the script in a clearly marked block — makes it easy for the scientist to tweak colors, sizes, labels without reading the analysis logic
- **Import shared utilities** for data loading and style — don't reinvent the wheel in each script
- **Comment the "why" not the "what"** — explain analytical decisions, not obvious code
- **Use descriptive variable names** — `accuracy_by_distance` not `d` or `result`
- **Print diagnostic info** — sample sizes, group counts, any filtering applied
