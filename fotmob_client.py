"""
fotmob_client.py
----------------
Cliente HTTP para FotMob. Gestiona sesiones, cookies y el buildId
necesario para los endpoints _next/data.

FotMob es una app Next.js: cada deploy cambia el buildId. Se obtiene
del HTML de la pagina principal y se refresca automaticamente si un
request devuelve 404.

Tambien expone un endpoint de API directa (/api/...) que NO necesita
buildId y es mas estable -- se usa como primera opcion.
"""

import re
import requests

TIMEOUT = 20

_BASE = "https://www.fotmob.com"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/html,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Cache global de sesion y buildId
_session = None
_build_id = None
_build_id_src = None  # "api" o "html"


def _obtener_sesion():
    """Sesion HTTP reutilizada con cookies warmup."""
    global _session
    if _session is None:
        _session = requests.Session()
        _calentar_cookies(_session)
    return _session


def _calentar_cookies(session):
    """Pide la portada para obtener cookies de Cloudflare/FotMob."""
    try:
        r = session.get(_BASE, headers=_HEADERS, timeout=TIMEOUT)
        print(f"[fotmob] warmup -> {r.status_code}")
    except Exception as e:
        print(f"[fotmob] warmup fallo: {e}")


def _obtener_build_id():
    """Obtiene el buildId actual del HTML de FotMob."""
    global _build_id, _build_id_src
    if _build_id:
        return _build_id

    session = _obtener_sesion()
    try:
        r = session.get(_BASE, headers=_HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        match = re.search(r'"buildId"\s*:\s*"([^"]+)"', r.text)
        if match:
            _build_id = match.group(1)
            _build_id_src = "html"
            print(f"[fotmob] buildId: {_build_id}")
            return _build_id
    except Exception as e:
        print(f"[fotmob] Error obteniendo buildId: {e}")

    return None


def _refrescar_build_id():
    """Fuerza refresco del buildId (ej. tras 404)."""
    global _build_id
    _build_id = None
    return _obtener_build_id()


def api_get(endpoint, params=None):
    """
    GET a la API directa de FotMob (/api/...).
    Devuelve el dict JSON o None si falla.
    """
    session = _obtener_sesion()
    url = f"{_BASE}/api/{endpoint}"
    try:
        r = session.get(url, params=params, headers=_HEADERS, timeout=TIMEOUT)
        if r.status_code == 404:
            print(f"[fotmob] API 404: {endpoint}")
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[fotmob] API error {endpoint}: {e}")
        return None


def next_data_get(ruta, intento=0):
    """
    GET via el patron _next/data de Next.js.
    'ruta' es relativa a la raiz (ej. '/matches/...').
    Devuelve el dict JSON o None si falla.
    """
    build_id = _obtener_build_id()
    if not build_id:
        print("[fotmob] No hay buildId disponible")
        return None

    session = _obtener_sesion()
    ruta_limpia = ruta.lstrip("/")
    url = f"{_BASE}/_next/data/{build_id}/en/{ruta_limpia}.json"
    try:
        r = session.get(url, headers=_HEADERS, timeout=TIMEOUT)
        if r.status_code == 404 and intento == 0:
            print(f"[fotmob] 404 en next/data, refrescando buildId...")
            _refrescar_build_id()
            return next_data_get(ruta, intento=1)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[fotmob] next/data error {ruta}: {e}")
        return None


def invalidar_build_id():
    """Llamar si se sospecha que el buildId ya no es valido."""
    global _build_id
    _build_id = None
