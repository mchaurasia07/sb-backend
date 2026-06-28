from pydantic import BaseModel, ConfigDict


class CreateSupportQueryRequest(BaseModel):
    subject: str | None = None
    query_details: str | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "subject": "Unable to generate my story",
                "query_details": "My story has been in Processing for more than 10 hours.",
            }
        }
    )


class AddSupportMessageRequest(BaseModel):
    message: str | None = None

    model_config = ConfigDict(
        json_schema_extra={"example": {"message": "Thank you for the update."}}
    )
