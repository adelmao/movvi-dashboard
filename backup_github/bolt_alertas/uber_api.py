import requests
import logging
import h3
from config import UBER_ORG_UUID, UBER_CITY_ID, UBER_COOKIES, UBER_EPH_MINIMO

log = logging.getLogger(__name__)

QUERY = """query getEarningsPredictions($cityId: Int!, $earningsType: EarningsType!, $filters: PredictHex9Filters, $pagination: Pagination) {
  getEarningsPredictions(cityId: $cityId earningsType: $earningsType filters: $filters pagination: $pagination) {
    predictions { hexagonId9 ephMean __typename }
    currencyCode
    __typename
  }
}"""

def obter_snapshot_uber() -> dict:
    """
    Retorna {h3_index_res8: ephMean_max} convertendo resolução 9 (Uber) para 8 (Bolt).
    Agrega o ephMean máximo de todas as células res9 dentro de cada célula res8.
    """
    headers = {
        "accept": "*/*",
        "content-type": "application/json",
        "origin": "https://supplier.uber.com",
        "referer": f"https://supplier.uber.com/orgs/{UBER_ORG_UUID}/livemap",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "x-csrf-token": "x",
        "cookie": UBER_COOKIES,
    }
    body = {
        "operationName": "getEarningsPredictions",
        "variables": {
            "cityId": UBER_CITY_ID,
            "earningsType": "EARNINGS_TYPE_ECONOMY_RIDES",
            "filters": {"hexagonsId_9": []},
            "pagination": {"pageSize": 500000, "pageToken": ""}
        },
        "query": QUERY
    }
    try:
        resp = requests.post("https://supplier.uber.com/graphql", headers=headers, json=body, timeout=15)
        resp.raise_for_status()
        dados = resp.json()
        predictions = dados.get("data", {}).get("getEarningsPredictions", {}).get("predictions", [])

        # Converte res9 → res8 e agrega ephMean máximo
        res8_eph = {}
        for p in predictions:
            h3_9 = p.get("hexagonId9", "")
            eph = p.get("ephMean", 0)
            if not h3_9 or eph <= 0:
                continue
            try:
                h3_8 = h3.cell_to_parent(h3_9, 8)
                if h3_8 not in res8_eph or eph > res8_eph[h3_8]:
                    res8_eph[h3_8] = round(eph, 2)
            except Exception:
                continue

        # Filtra pelo mínimo
        resultado = {k: v for k, v in res8_eph.items() if v >= UBER_EPH_MINIMO}
        log.info(f"Uber: {len(predictions)} celulas res9 → {len(res8_eph)} res8 → {len(resultado)} acima de {UBER_EPH_MINIMO}€/h")
        return resultado

    except Exception as e:
        log.error(f"Erro Uber API: {e}")
        return {}
