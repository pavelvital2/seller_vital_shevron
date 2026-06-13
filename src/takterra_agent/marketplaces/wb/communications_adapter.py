from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from ...config import WbCredentials
from ...http import request_json


@dataclass
class WbCommunicationsAdapter:
    credentials: WbCredentials
    base_url: str = "https://feedbacks-api.wildberries.ru"

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": self.credentials.token}

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        query = f"?{urlencode(params)}" if params else ""
        return request_json("GET", f"{self.base_url}{path}{query}", headers=self.headers)

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        return request_json("POST", f"{self.base_url}{path}", headers=self.headers, payload=payload)

    def patch(self, path: str, payload: dict[str, Any]) -> Any:
        return request_json("PATCH", f"{self.base_url}{path}", headers=self.headers, payload=payload)

    def fetch_unanswered_feedbacks_count(self) -> dict[str, Any]:
        data = self.get("/api/v1/feedbacks/count-unanswered")
        return data if isinstance(data, dict) else {"data": data}

    def fetch_unanswered_questions_count(self) -> dict[str, Any]:
        data = self.get("/api/v1/questions/count-unanswered")
        return data if isinstance(data, dict) else {"data": data}

    def fetch_feedbacks(
        self,
        *,
        is_answered: bool = False,
        take: int = 5000,
        skip: int = 0,
        order: str = "dateDesc",
    ) -> dict[str, Any]:
        data = self.get(
            "/api/v1/feedbacks",
            {
                "isAnswered": str(is_answered).lower(),
                "take": take,
                "skip": skip,
                "order": order,
            },
        )
        return data if isinstance(data, dict) else {"data": data}

    def fetch_questions(
        self,
        *,
        is_answered: bool = False,
        take: int = 10000,
        skip: int = 0,
        order: str = "dateDesc",
    ) -> dict[str, Any]:
        data = self.get(
            "/api/v1/questions",
            {
                "isAnswered": str(is_answered).lower(),
                "take": take,
                "skip": skip,
                "order": order,
            },
        )
        return data if isinstance(data, dict) else {"data": data}

    def answer_feedback(self, *, feedback_id: str, text: str) -> dict[str, Any]:
        data = self.post("/api/v1/feedbacks/answer", {"id": feedback_id, "text": text})
        return data if isinstance(data, dict) else {"data": data}

    def answer_question(self, *, question_id: str, text: str, state: str = "wbRu") -> dict[str, Any]:
        data = self.patch("/api/v1/questions", {"id": question_id, "answer": {"text": text}, "state": state})
        return data if isinstance(data, dict) else {"data": data}
