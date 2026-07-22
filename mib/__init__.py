"""MIB doc challenge pipeline.

Module layout mirrors the trust boundaries of the problem:

    pdfio    — evidence intake: PDF → per-page visible/hidden text (injection quarantine)
    parse    — interpretation: doc-type detection, key/value parsing, vocabularies
    packet   — case assembly: active case id, document census, field merge by precedence
    signals  — derived fraud signals (see docs/fraud-signals.md)
    policy   — adjudication rule engine (named branches, field-manual order)
    confidence — branch → calibrated confidence
    emit     — record assembly + output validation (schema safety net)
"""
