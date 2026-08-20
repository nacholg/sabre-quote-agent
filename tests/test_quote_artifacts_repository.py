from app.services.quote_repository import QuoteRepository


def test_artifacts_can_be_created_listed_and_deleted(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = "Q-ARTIFACT-TEST"

    first = repo.create_artifact(
        quote_id,
        artifact_type="whatsapp",
        title="WhatsApp",
        selected_ranks=[2, 1, 2],
        content_type="text/plain",
        content="Hola",
    )
    second = repo.create_artifact(
        quote_id,
        artifact_type="email",
        title="Email",
        selected_ranks=[1],
        content_type="text/html",
        content="<p>Hola</p>",
    )

    rows = repo.list_artifacts(quote_id)
    assert [row["artifact_id"] for row in rows] == [
        second["artifact_id"],
        first["artifact_id"],
    ]
    assert rows[0]["selected_ranks"] == [1]
    assert rows[1]["selected_ranks"] == [1, 2]
    assert rows[1]["content"] == "Hola"

    assert repo.delete_artifact(quote_id, first["artifact_id"])
    assert len(repo.list_artifacts(quote_id)) == 1

    assert repo.clear_artifacts(quote_id) == 1
    assert repo.list_artifacts(quote_id) == []
