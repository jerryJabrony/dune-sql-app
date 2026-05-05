import os
import re
import uuid
import httpx
from fastapi import FastAPI, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=os.urandom(24))
templates = Jinja2Templates(directory="templates")

# Конфигурация Dune OAuth (получите эти данные на dune.com)
DUNE_CLIENT_ID = os.environ.get("DUNE_CLIENT_ID", "")
DUNE_CLIENT_SECRET = os.environ.get("DUNE_CLIENT_SECRET", "")
DUNE_REDIRECT_URI = os.environ.get("DUNE_REDIRECT_URI", "https://dune-sql-app-production.up.railway.app/auth/callback")

# Простая база сессий (для хранения токенов между запросами)
sessions_store = {}

def is_valid_query(text: str) -> tuple[bool, str]:
    """Проверка на пустой/нелепый запрос"""
    if not text or len(text.strip()) < 5:
        return False, "Запрос слишком короткий"
    nonsense = [
        "привет", "как дела", "погода", "рецепт", "расскажи",
        "анекдот", "что ты думаешь", "ты кто", "помоги"
    ]
    lower = text.lower()
    for word in nonsense:
        if word in lower:
            return False, f"Запрос '{word}' не относится к аналитике блокчейна"
    return True, ""

def natural_to_sql(user_input: str) -> str:
    """Преобразует естественный язык в SQL для Dune (умная заглушка)"""
    user_input = user_input.lower()
    
    # Примеры интеллектуального преобразования
    if "топ" in user_input and "пулов" in user_input and "стейблкоин" in user_input:
        return """
SELECT 
    pool_name,
    apy,
    tvl_usd
FROM dune.defi.lending_pools
WHERE pool_type = 'stablecoin'
  AND date >= CURRENT_DATE - INTERVAL '7' DAY
ORDER BY apy DESC
LIMIT 10;
"""
    elif "транзакций" in user_input and "ethereum" in user_input:
        return """
SELECT 
    date_trunc('day', block_time) AS day,
    COUNT(*) AS txn_count
FROM dune.ethereum.transactions
WHERE block_time >= CURRENT_DATE - INTERVAL '7' DAY
GROUP BY 1
ORDER BY 1 DESC;
"""
    elif "gas" in user_input or "газа" in user_input:
        return """
SELECT 
    contract_address,
    SUM(gas_used) AS total_gas
FROM dune.ethereum.transactions
WHERE block_time >= CURRENT_DATE - INTERVAL '1' DAY
GROUP BY 1
ORDER BY total_gas DESC
LIMIT 10;
"""
    elif "цена eth" in user_input or "динамика" in user_input:
        return """
SELECT 
    date_trunc('day', minute) AS day,
    AVG(price) AS eth_price
FROM dune.prices.eth
WHERE minute >= CURRENT_DATE - INTERVAL '30' DAY
GROUP BY 1
ORDER BY 1;
"""
    else:
        # Базовый запрос
        return f"""
-- Ваш запрос: {user_input}
SELECT 
    date_trunc('day', block_time) AS day,
    COUNT(*) AS activity_count
FROM dune.ethereum.transactions
WHERE block_time >= CURRENT_DATE - INTERVAL '7' DAY
GROUP BY 1
ORDER BY 1;
"""

# ------------------- Dune OAuth -------------------
@app.get("/auth/dune")
async def auth_dune(request: Request):
    """Начало OAuth потока"""
    if not DUNE_CLIENT_ID:
        return HTMLResponse("Ошибка: не настроен DUNE_CLIENT_ID. Добавьте переменные окружения.", status_code=500)
    state = str(uuid.uuid4())
    request.session["oauth_state"] = state
    auth_url = f"https://dune.com/oauth/authorize?client_id={DUNE_CLIENT_ID}&redirect_uri={DUNE_REDIRECT_URI}&response_type=code&state={state}"
    return RedirectResponse(auth_url)

@app.get("/auth/callback")
async def auth_callback(request: Request, code: str, state: str):
    """Callback после авторизации"""
    stored_state = request.session.get("oauth_state")
    if not stored_state or stored_state != state:
        raise HTTPException(400, "Invalid OAuth state")
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://dune.com/oauth/token",
            data={
                "client_id": DUNE_CLIENT_ID,
                "client_secret": DUNE_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": DUNE_REDIRECT_URI,
            }
        )
        if resp.status_code != 200:
            raise HTTPException(400, "Failed to exchange token")
        token_data = resp.json()
    
    session_id = str(uuid.uuid4())
    sessions_store[session_id] = {
        "access_token": token_data["access_token"],
        "user_id": token_data.get("user_id", "unknown")
    }
    request.session["session_id"] = session_id
    return RedirectResponse("/query")

# ------------------- Страницы -------------------
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "index.html", {"request": request})

@app.get("/query", response_class=HTMLResponse)
async def query_form(request: Request):
    session_id = request.session.get("session_id")
    if not session_id or session_id not in sessions_store:
        return RedirectResponse("/")
    return templates.TemplateResponse(request, "query.html", {"request": request})

@app.post("/translate")
async def translate(
    request: Request,
    query_text: str = Form(...)
):
    session_id = request.session.get("session_id")
    if not session_id or session_id not in sessions_store:
        return RedirectResponse("/", status_code=303)
    
    # Валидация запроса
    valid, error = is_valid_query(query_text)
    if not valid:
        return HTMLResponse(f"<h3>❌ Ошибка: {error}</h3><a href='/query'>Назад</a>", status_code=400)
    
    # Генерация SQL
    sql = natural_to_sql(query_text)
    
    # Создание запроса в Dune через API
    access_token = sessions_store[session_id]["access_token"]
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.dune.com/api/v1/query",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json={
                "query_sql": sql,
                "name": f"Auto: {query_text[:50]}",
                "description": f"Создано переводчиком SQL. Исходный запрос: {query_text}"
            }
        )
        if resp.status_code == 200 or resp.status_code == 201:
            data = resp.json()
            query_id = data.get("query_id")
            if query_id:
                return RedirectResponse(f"https://dune.com/queries/{query_id}/results")
        # Если API не сработал, шлём на ручное создание
        import urllib.parse
        encoded_sql = urllib.parse.quote(sql)
        return RedirectResponse(f"https://dune.com/new_query?sql={encoded_sql}")

# Запуск (для локальной разработки)
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
