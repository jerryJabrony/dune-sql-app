from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import os
import uuid

app = FastAPI()
templates = Jinja2Templates(directory="templates")

sessions = {}

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    # Правильный вызов: request первым аргументом
    return templates.TemplateResponse(request, "index.html", {"request": request})

@app.get("/auth/dune")
async def auth_dune():
    session_id = str(uuid.uuid4())
    sessions[session_id] = {"user_id": "demo"}
    return RedirectResponse(f"/query?session_id={session_id}")

@app.get("/query", response_class=HTMLResponse)
async def query_form(request: Request, session_id: str):
    if session_id not in sessions:
        return RedirectResponse("/")
    return templates.TemplateResponse(
        request, 
        "query.html", 
        {"request": request, "session_id": session_id}
    )

@app.post("/translate")
async def translate_query(session_id: str = Form(...), query_text: str = Form(...)):
    if session_id not in sessions:
        return RedirectResponse("/")
    
    if not query_text or len(query_text.strip()) < 5:
        return HTMLResponse("Ошибка: запрос слишком короткий", status_code=400)
    
    sql_query = f"""
-- Ваш запрос: {query_text}
SELECT 
    date_trunc('day', block_time) as day,
    COUNT(*) as transaction_count,
    SUM(gas_used) as total_gas
FROM dune.ethereum.transactions
WHERE block_time >= CURRENT_DATE - INTERVAL '7' DAY
GROUP BY 1
ORDER BY 1 DESC
LIMIT 100;
"""
    return HTMLResponse(f"""
    <html>
        <head><title>SQL готов</title></head>
        <body style="font-family: monospace; padding: 20px;">
            <h2>✅ SQL запрос сгенерирован</h2>
            <p><strong>Ваш запрос:</strong> {query_text}</p>
            <p><strong>SQL для Dune:</strong></p>
            <pre style="background: #f0f0f0; padding: 15px; border-radius: 5px;">{sql_query}</pre>
            <a href="/query?session_id={session_id}">← Новый запрос</a>
        </body>
    </html>
    """)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
