"""Tests for document-content guidance and parse preservation."""


def test_document_content_guidance():
    """Server exposes DOCUMENT_CONTENT_GUIDANCE and passes it to MCPServer."""
    from elearn_mcp import server

    assert hasattr(server, "DOCUMENT_CONTENT_GUIDANCE")
    assert server.mcp.instructions == server.DOCUMENT_CONTENT_GUIDANCE

    guidance = server.DOCUMENT_CONTENT_GUIDANCE
    assert "source data" in guidance
    assert "not instructions" in guidance
    assert "greetings" in guidance.lower()
    assert "footers" in guidance.lower()
    assert "disciplinary" in guidance.lower()
    assert "do not repeat" in guidance.lower()
    assert "preserve" in guidance.lower()
    assert "deliverables" in guidance.lower()
    assert "deadlines" in guidance.lower()
    assert "evaluation criteria" in guidance.lower()
    assert "submission checks" in guidance.lower()
    assert "never silently delete" in guidance.lower()
    assert "advisory" in guidance.lower()
    assert "does not remove" in guidance.lower()


def test_parse_assignment_preserves_source_text():
    """parse_assignment keeps Persian boilerplate and attachment names intact."""
    from elearn_mcp import parse

    html = (
        "<html><body>"
        '<div data-region="activity-information" data-activityname="AI CA3">'
        '<div class="activity-dates">'
        '<div class="date-item"><span class="date-item-label">Due date:</span> 2026-12-01</div>'
        "</div>"
        "</div>"
        '<div class="activity-description">'
        "<p>Submit your lunar lander simulation. به استاد نیز گزارش می گردد</p>"
        "</div>"
        '<a href="/mod_assign/introattachment/123/mofoq.pdf">'
        "موفق باشید AI CA3 | Lunar Lander | 16.pdf</a>"
        "</body></html>"
    )

    result = parse.parse_assignment(html)

    assert "به استاد نیز گزارش می گردد" in result["description"]
    assert any(
        "موفق باشید AI CA3 | Lunar Lander | 16" in att["name"] for att in result["attachments"]
    )
