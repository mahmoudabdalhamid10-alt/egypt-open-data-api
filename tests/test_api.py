from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)

def test_fuel():
    d = c.get("/v1/fuel/latest").json()
    assert d["prices"]["gasoline_92"]["price"] > 5 and "source" in d

def test_fx():
    d = c.get("/v1/fx/latest?symbols=USD,EUR").json()
    assert set(d["rates_egp"]) == {"USD", "EUR"} and d["rates_egp"]["USD"] > 20

def test_macro():
    d = c.get("/v1/macro/inflation?from_year=2020").json()
    assert d["latest"]["year"] >= 2024 and d["series"][0]["year"] >= 2020

def test_bad():
    assert c.get("/v1/macro/nope").status_code == 404
    assert c.get("/v1/fx/notadate").status_code == 400
