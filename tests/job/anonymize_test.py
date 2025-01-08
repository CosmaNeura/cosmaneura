from app.jobs.anonymize import deidentify

def test_anonymize():
    deid_string = deidentify("John Smith had bypass surgery in St. Francis hospital on June 12, 2013.")

    assert "John" not in deid_string
    assert "Smith" not in deid_string
    assert "St. Francis" not in deid_string
    assert "June 12" not in deid_string
