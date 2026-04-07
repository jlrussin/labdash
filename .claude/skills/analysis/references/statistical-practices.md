# Statistical Practices Reference

## Choosing the Right Test

| Question | Data type | Recommended test | Effect size |
|----------|-----------|-----------------|-------------|
| Two group means | Continuous, normal | Independent t-test | Cohen's d |
| Two group means | Non-normal or ordinal | Mann-Whitney U | rank-biserial r |
| Paired comparison | Continuous, normal | Paired t-test | Cohen's d_z |
| Paired comparison | Non-normal | Wilcoxon signed-rank | matched-pairs r |
| >2 group means | Continuous, normal | One-way ANOVA | eta-squared |
| >2 group means | Non-normal | Kruskal-Wallis | epsilon-squared |
| Repeated measures | Continuous, normal | Repeated-measures ANOVA | partial eta-squared |
| Two categorical | Counts | Chi-squared test | Cramer's V |
| Association | Continuous, linear | Pearson correlation | r |
| Association | Non-linear or ordinal | Spearman correlation | rho |
| Prediction | Continuous outcome | Linear regression | R-squared, beta |
| Prediction | Binary outcome | Logistic regression | Odds ratio |
| Mixed design | Continuous | Mixed-effects model | Conditional R-squared |

## Assumption Checks

### Normality
- **Visual**: Q-Q plot, histogram with normal overlay
- **Test**: Shapiro-Wilk (N < 50), Anderson-Darling, or Kolmogorov-Smirnov
- **When violated**: Consider non-parametric alternatives, or note that the test is robust to violations with sufficient N (central limit theorem applies for N > 30 in many cases)

### Homogeneity of Variance
- **Test**: Levene's test
- **When violated**: Use Welch's t-test instead of Student's, or Games-Howell for post-hoc comparisons

### Sphericity (repeated measures)
- **Test**: Mauchly's test
- **When violated**: Apply Greenhouse-Geisser or Huynh-Feldt correction

### Independence
- Not testable statistically — must be ensured by design
- Flag if trials within a participant might be correlated (learning effects, fatigue)

## Multiple Comparisons

### When to correct
- Multiple pairwise comparisons after a significant omnibus test
- Multiple DVs tested on the same data
- Multiple sub-group analyses

### When correction may not be needed
- Pre-planned, theoretically motivated contrasts (but declare them)
- Exploratory analysis (but label it as exploratory)

### Methods (from most to least conservative)
1. **Bonferroni**: p_adj = p × n_tests. Simple, conservative, appropriate for small number of tests
2. **Holm-Bonferroni**: Step-down procedure. Uniformly more powerful than Bonferroni. Preferred default
3. **Benjamini-Hochberg (FDR)**: Controls false discovery rate. Appropriate for large numbers of tests (e.g., neuroimaging)
4. **Tukey HSD**: Specifically for all pairwise comparisons after ANOVA

## Reporting Conventions (APA 7th Edition)

### Format
- Italicize test statistics: *t*, *F*, *r*, *p*, *d*, *η²*
- Report exact p-values to 3 decimal places, except when p < .001
- Always include degrees of freedom
- Include 95% CI for effect sizes when possible

### Examples
- t-test: *t*(48) = 2.35, *p* = .023, *d* = 0.67, 95% CI [0.09, 1.24]
- ANOVA: *F*(2, 57) = 4.12, *p* = .021, *η²* = .13
- Correlation: *r*(38) = .52, *p* < .001, 95% CI [.24, .72]
- Chi-squared: *χ²*(1, *N* = 100) = 5.23, *p* = .022, *V* = .23

## Power and Sample Size

- Report observed power only when results are non-significant (and note its limitations)
- For planning: target 80% power at the minimum effect size of interest
- With small N, be explicit: "With N = X, this analysis had Y% power to detect an effect of size Z"
- Cohen's conventions for "small/medium/large" are rules of thumb, not laws — use domain knowledge to define the minimum effect size of practical interest

## Common Pitfalls to Flag

1. **Dichotomizing continuous variables** (median split) — loses information, inflates Type I error
2. **Treating ordinal as interval** without justification
3. **Removing outliers without pre-specified criteria** — state the rule before looking at data
4. **Interpreting non-significance as "no effect"** — absence of evidence ≠ evidence of absence
5. **Double-dipping** — using the same data to select and test a hypothesis
6. **Ignoring crossed random effects** — when both participants and items vary, use mixed-effects models
