from settleezy_cofounder.knowledge import ONE_LINER, facts, playbook


def test_facts_skip_unfilled_todos_and_cover_the_product():
    text = facts()
    assert "TODO" not in text
    for must in ("€40", "€70", "30 days", "grocery", "Buddy platform", "accommodation", "document vault", "workshop"):
        assert must.lower() in text.lower(), must


def test_one_liner_and_playbook():
    assert "€40 per semester" in ONE_LINER and "Buddy platform" in ONE_LINER
    assert "Buddy platform" in playbook()
