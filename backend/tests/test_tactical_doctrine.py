from backend.prompts.tactical_doctrine import get_tactical_doctrine, available_doctrine_topics


def test_available_doctrine_topics_include_new_command_families():
    topics = available_doctrine_topics()
    assert "general" in topics
    assert "engineers" in topics
    assert "logistics" in topics
    assert "split_merge" in topics
    assert "map_objects" in topics


def test_brief_doctrine_topic_subset_is_scoped():
    doctrine = get_tactical_doctrine("brief", topics=["engineers", "map_objects"])

    assert "Topic: Engineers" in doctrine
    assert "Topic: Map Objects" in doctrine
    assert "Topic: Logistics" not in doctrine


def test_full_doctrine_can_be_composed_from_topics():
    doctrine = get_tactical_doctrine("full", topics=["fires", "recon"])

    assert "Topic: Fires" in doctrine
    assert "Topic: Recon" in doctrine
    assert "Topic: General" in doctrine
