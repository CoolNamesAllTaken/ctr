from ninja import Router, Schema

router = Router()


class HealthOut(Schema):
    status: str


@router.get("/health/", response=HealthOut, auth=None)
def health(request):
    return {"status": "ok"}
