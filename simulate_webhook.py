import httpx
import asyncio

async def main():
    url = "http://127.0.0.1:8000/api/webhook/00000000-0000-0000-0000-000000000000"
    
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_ID",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550000000",
                                "phone_number_id": "phone_id_123"
                            },
                            "contacts": [
                                {
                                    "profile": {
                                        "name": "Pedro Pereira"
                                    },
                                    "wa_id": "5511999999999"
                                }
                            ],
                            "messages": [
                                {
                                    "from": "5511999999999",
                                    "id": "wamid.HBgLNTUxMTk5OTk5OTk5OQYSFh8BCBJDQzRENUY2RTk4RDNBQUIA",
                                    "timestamp": "1672531199",
                                    "type": "text",
                                    "text": {
                                        "body": "Olá, gostaria de saber se vocês aceitam pets no resort?"
                                    }
                                }
                            ]
                        },
                        "field": "messages"
                    }
                ]
            }
        ]
    }
    
    print("Enviando simulação de webhook...")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json=payload)
            print(f"Status Code: {response.status_code}")
            print(f"Resposta: {response.text}")
        except Exception as e:
            print(f"Erro ao conectar ao servidor local: {e}")

if __name__ == "__main__":
    asyncio.run(main())
