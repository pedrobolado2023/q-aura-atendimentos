"""
Q-Aura Backend — Script de inicialização simples.
Execute com: python run.py
"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,          # Hot reload em desenvolvimento
        log_level="info",
        access_log=True
    )
