from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.translation_service import translate_texts

router = APIRouter()


class TranslationRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=50)
    target_language: Literal["en", "ar"]


@router.post("")
async def translate(payload: TranslationRequest) -> dict:
    texts = [text.strip()[:2000] for text in payload.texts]
    translations, provider = await translate_texts(texts, payload.target_language)
    return {"translations": translations, "provider": provider}
