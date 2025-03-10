from fastapi import APIRouter, File, UploadFile

router = APIRouter()

@router.post("/upload/")
def upload_file(file: UploadFile = File(...)):
    """
    Загрузка файла. Возвращает информацию о загруженном файле.
    """
    return {"filename": file.filename, "content_type": file.content_type}
