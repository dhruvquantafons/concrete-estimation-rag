"""Learning loop: the system improves from use, under human control.

    use (search / ask / estimate)  ->  feedback (👍/👎, corrections, actual costs)
        ->  candidate update (fusion weights, fine-tuned reranker, answer exemplars, estimate calibration)
        ->  gate (golden eval sets must not get worse)  ->  promote (versioned, rollback-able)

What can learn: retrieval ranking, answer style, thumb-rule estimate assumptions.
What never learns: IS code values, DSR rates, calculator formulas - those change only by human correction
(store/review.py, config/corrections, config/code_tables sign-off).
"""
