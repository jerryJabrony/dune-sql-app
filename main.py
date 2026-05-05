# main.py
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
import httpx
import uuid
import json
import re
from typing import Optional, Dict
from pydantic import BaseModel
import secrets

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Хранилище сессий (в проде использовать Redis)
sessions = {}

class DuneAuth:
    """Управление авторизацией в Dune"""
    DUNE_CLIENT_ID = "your_client_id"  # Регистрируем приложение в Dune
    DUNE_CLIENT_SECRET = "your_client_secret"
    DUNE_REDIRECT_URI = "http://localhost:8000/auth/callback"
    
    @staticmethod
    def get_auth_url() -> str:
        state = secrets.token_urlsafe(32)
        return (f"https://dune.com/oauth/authorize"
                f"?client_id={DuneAuth.DUNE_CLIENT_ID}"
                f"&redirect_uri={DuneAuth.DUNE_REDIRECT_URI}"
                f"&response_type=code"
                f"&state={state}")
    
    @staticmethod
    async def exchange_code(code: str) -> Dict:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://dune.com/oauth/token",
                data={
                    "client_id": DuneAuth.DUNE_CLIENT_ID,
                    "client_secret": DuneAuth.DUNE_CLIENT_SECRET,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": DuneAuth.DUNE_REDIRECT_URI
                }
            )
            return response.json()

class QueryTranslator:
    """Перевод текстового запроса в SQL Dune"""
    
    @staticmethod
    def is_valid_query(query: str) -> tuple[bool, str]:
        """Валидация запроса"""
        if not query or len(query.strip()) < 5:
            return False, "Запрос слишком короткий"
            
        invalid_keywords = [
            "привет", "как дела", "погода", "рецепт",
            "расскажи", "объясни", "кто ты"
        ]
        
        query_lower = query.lower()
        for keyword in invalid_keywords:
            if keyword in query_lower:
                return False, f"Запрос содержит недопустимое слово: {keyword}"
                
        return True, "OK"
    
    @staticmethod
    async def natural_to_sql(user_query: str, access_token: str) -> str:
        """Преобразование естественного языка в SQL через LLM"""
        
        # Шаблон для Dune SQL
        prompt = f"""
        Преобразуй следующий запрос в SQL для Dune Analytics.
        Запрос: "{user_query}"
        
        Требования:
        - Используй только таблицы Dune (dune.{schema}.{table})
        - Добавь LIMIT 100 для безопасности
        - Учитывай синтаксис Dune (Trino/Spark SQL)
        
        Примеры:
        Запрос: "показать топ 10 кошельков по объему торгов на Uniswap за последний день"
        SQL: 
        SELECT wallet_address, SUM(volume_usd) as total_volume
        FROM dune.uniswap_data.trades
        WHERE date >= CURRENT_DATE - INTERVAL '1' DAY
        GROUP BY wallet_address
        ORDER BY total_volume DESC
        LIMIT 100;
        
        Ответь только SQL запросом, без пояснений.
        """
        
        # Здесь используем OpenAI или другую LLM
        # В продакшене заменить на реальный вызов
        mock_sql = f"""
        -- Автоматически сгенерированный SQL из запроса: {user_query}
        WITH filtered_data AS (
            SELECT *
            FROM dune.ethereum.transactions
            WHERE block_time >= CURRENT_DATE - INTERVAL '7' DAY
        )
        SELECT 
            date_trunc('day', block_time) as day,
            COUNT(*) as transaction_count,
            SUM(gas_used) as total_gas
        FROM filtered_data
        GROUP BY 1
        ORDER BY 1 DESC
        LIMIT 100;
        """
        
        return mock_sql

class QuerySanitizer:
    """Очистка и валидация SQL"""
    
    @staticmethod
    def sanitize_sql(sql: str) -> str:
        """Безопасная очистка SQL"""
        # Удаляем опасные конструкции
        dangerous = ['DROP', 'DELETE', 'UPDATE', 'INSERT', 'ALTER', 'CREATE', 'TRUNCATE']
        sql_upper = sql.upper()
        
        for keyword in dangerous:
            if keyword in sql_upper:
                # Добавляем комментарий вместо исполнения
                sql = sql.replace(keyword, f"-- {keyword}")
                
        return sql
    
    @staticmethod
    def validate_dune_sql(sql: str) -> tuple[bool, str]:
        """Валидация SQL для Dune"""
        sql_upper = sql.upper()
        
        required = ['SELECT', 'FROM']
        for req in required:
            if req not in sql_upper:
                return False, f"Отсутствует ключевое слово {req}"
        
        if 'LIMIT' not in sql_upper:
            sql += "\nLIMIT 1000"
            
        return True, sql

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Главная страница с авторизацией"""
    return templates.TemplateResponse(
        "index.html",
        {"request": request}
    )

@app.get("/auth/dune")
async def auth_dune():
    """Перенаправление на авторизацию Dune"""
    auth_url = DuneAuth.get_auth_url()
    return RedirectResponse(auth_url)

@app.get("/auth/callback")
async def auth_callback(code: str, state: str):
    """Обработка callback от Dune"""
    try:
        # Обмениваем код на токен
        token_data = await DuneAuth.exchange_code(code)
        
        # Создаем сессию пользователя
        session_id = str(uuid.uuid4())
        sessions[session_id] = {
            "access_token": token_data.get("access_token"),
            "user_id": token_data.get("user_id", "unknown")
        }
        
        return RedirectResponse(f"/query?session_id={session_id}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Auth failed: {str(e)}")

@app.get("/query", response_class=HTMLResponse)
async def query_form(request: Request, session_id: str):
    """Форма ввода запроса"""
    if session_id not in sessions:
        return RedirectResponse("/")
        
    return templates.TemplateResponse(
        "query.html",
        {"request": request, "session_id": session_id}
    )

@app.post("/translate")
async def translate_query(session_id: str, query_text: str):
    """Перевод запроса в SQL и редирект в Dune"""
    
    # Проверка сессии
    if session_id not in sessions:
        raise HTTPException(401, "Сессия не найдена")
    
    # Валидация запроса
    is_valid, error_message = QueryTranslator.is_valid_query(query_text)
    if not is_valid:
        raise HTTPException(400, f"Невалидный запрос: {error_message}")
    
    # Перевод в SQL
    access_token = sessions[session_id]["access_token"]
    sql_query = await QueryTranslator.natural_to_sql(query_text, access_token)
    
    # Очистка и валидация SQL
    sql_clean = QuerySanitizer.sanitize_sql(sql_query)
    is_valid_sql, processed_sql = QuerySanitizer.validate_dune_sql(sql_clean)
    
    if not is_valid_sql:
        raise HTTPException(400, f"Ошибка валидации SQL: {processed_sql}")
    
    # Сохраняем запрос в сессии
    sessions[session_id]["last_query"] = {
        "original": query_text,
        "sql": processed_sql
    }
    
    # Создаем query в Dune через API
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.dune.com/api/v1/query",
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "query_sql": processed_sql,
                "name": f"Query: {query_text[:50]}",
                "description": f"Auto-generated from: {query_text}"
            }
        )
        
        if response.status_code == 200:
            query_data = response.json()
            query_id = query_data.get("query_id")
            
            # Перенаправляем пользователя в Dune на результат
            return RedirectResponse(
                f"https://dune.com/queries/{query_id}/results"
            )
        else:
            # Если API не работает, перенаправляем с SQL в URL
            import urllib.parse
            encoded_sql = urllib.parse.quote(processed_sql)
            return RedirectResponse(
                f"https://dune.com/new_query?sql={encoded_sql}"
            )