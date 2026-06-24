from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from models.database import get_db
from schemas.license_schema import LicenseActivate, LicenseVerify, LicenseIssue, LicenseExtend, LicenseInfo
from services.license_service import activate_license, verify_license, revoke_license, issue_license, extend_license
from routers.admin import get_current_admin

router = APIRouter(prefix="/api/license", tags=["License"])


@router.post("/activate")
def activate(data: LicenseActivate, db: Session = Depends(get_db)):
    return activate_license(db, data)


@router.get("/verify")
def verify(license_key: str, machine_id: str, db: Session = Depends(get_db)):
    data = LicenseVerify(license_key=license_key, machine_id=machine_id)
    return verify_license(db, data)


@router.post("/revoke")
def revoke(license_key: str, db: Session = Depends(get_db), _=Depends(get_current_admin)):
    return revoke_license(db, license_key)


@router.post("/issue", response_model=LicenseInfo)
def issue(data: LicenseIssue, db: Session = Depends(get_db), _=Depends(get_current_admin)):
    return issue_license(db, data)


@router.put("/extend", response_model=LicenseInfo)
def extend(data: LicenseExtend, db: Session = Depends(get_db), _=Depends(get_current_admin)):
    return extend_license(db, data)
