"""Pipeline stages, in order. See mib/records.py for what crosses each seam.

    extract   PDF         -> Page[]        pure, cheap
    render    Page        -> Read[]        IMPURE, EXPENSIVE  <- cache boundary
    parse     Read[]      -> Candidate[]   pure, cheap
    assemble  Candidate[] -> CaseRow       pure, cheap
    decide    CaseRow     -> Decision      pure, cheap

Stages do not call each other; `mib/runner.py` sequences them. That is what lets
everything downstream of `render` be re-run from a cache in seconds instead of
re-paying for OCR, which is ~95% of runtime.
"""
