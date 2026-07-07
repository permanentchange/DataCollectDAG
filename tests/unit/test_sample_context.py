from data_collect_dag.sample_context import AmbiguousContextKeyError, MissingContextKeyError, SampleContext


def test_sample_context_put_get_remove():
    sample = SampleContext(sample_id="s1")
    sample.put("main_frame", 1, "start")
    assert sample.get("main_frame") == 1
    sample.remove("main_frame")
    assert not sample.has("main_frame")


def test_sample_context_ambiguous():
    sample = SampleContext(sample_id="s1")
    sample.put("k", 1, "a")
    sample.put("k", 2, "b")
    try:
        sample.get("k")
    except AmbiguousContextKeyError:
        pass
    else:
        raise AssertionError("expected ambiguous error")
    assert sample.get("k", producer="a") == 1
    try:
        sample.get("missing")
    except MissingContextKeyError:
        pass
    else:
        raise AssertionError("expected missing error")

