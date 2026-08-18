from app.services.reference_repository import ReferenceRepository, seed_reference_data


def test_seed_reference_data_resolves_airlines_and_cities(tmp_path):
    repo=ReferenceRepository(tmp_path/"reference.db")
    seed_reference_data(repo)
    assert repo.resolve_exact("Turkish","airline") == ["TK"]
    assert repo.resolve_exact("Air Europa","airline") == ["UX"]
    assert repo.resolve_exact("San Pablo","city") == ["SAO"]
    assert set(repo.airports_for_city("SAO")) >= {"GRU","CGH","VCP"}


def test_reference_data_can_be_extended_without_parser_code_changes(tmp_path):
    repo=ReferenceRepository(tmp_path/"reference.db")
    repo.upsert_airline(code="ZZ",name="Example Airways",source="test")
    repo.add_alias("Ejemplo Air","airline","ZZ",source="test")
    assert repo.resolve_exact("Ejemplo Air","airline") == ["ZZ"]
