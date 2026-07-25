from pydantic import BaseModel


class StudyRecord(BaseModel):
    PatientName: str
    PatientID: str
    PatientBirthDate: str
    PatientAge: str
    PatientSex: str
    InstitutionName: str
    StudyID: str
    StudyInstanceUID: str
    StudyDate: str
    Modality: str
    BodyPartExamined: str
    Diagnosis: str
