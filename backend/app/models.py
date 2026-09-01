from datetime import datetime, timezone
from sqlalchemy.types import TypeDecorator, String
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from . import db


class UUIDString(TypeDecorator):
    """Store UUIDs natively on PostgreSQL while remaining SQLite/MySQL compatible."""
    impl = String(36)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PostgreSQLUUID(as_uuid=False))
        return dialect.type_descriptor(String(36))


def now(): return datetime.now(timezone.utc)

class User(db.Model):
    __tablename__='user'
    id=db.Column(db.Integer,primary_key=True); agency_id=db.Column(db.Integer,db.ForeignKey('agency_company.id'),index=True,nullable=True)
    username=db.Column(db.String(80),unique=True,nullable=False,index=True)
    supabase_user_id=db.Column(UUIDString(),unique=True,index=True)
    password_hash=db.Column(db.String(255),nullable=False); full_name=db.Column(db.String(160),nullable=False)
    role=db.Column(db.String(30),nullable=False,default='REPO_AGENT',index=True); phone=db.Column(db.String(30),unique=True,index=True)
    email=db.Column(db.String(160),unique=True,index=True); active=db.Column(db.Boolean,default=True,nullable=False)
    is_deleted=db.Column(db.Boolean,default=False,index=True); device_id=db.Column(db.String(255),unique=True,index=True)
    created_at=db.Column(db.DateTime,default=now)

class UserOTP(db.Model):
    __tablename__='user_otp'
    id=db.Column(db.Integer,primary_key=True); agency_id=db.Column(db.Integer,db.ForeignKey('agency_company.id'),index=True,nullable=True)
    email=db.Column(db.String(160)); phone=db.Column(db.String(30)); otp_hash=db.Column(db.String(255),nullable=False)
    expires_at=db.Column(db.DateTime,nullable=False); used=db.Column(db.Boolean,default=False,nullable=False); created_at=db.Column(db.DateTime,default=now)

class Bank(db.Model):
    id=db.Column(db.Integer,primary_key=True); agency_id=db.Column(db.Integer,db.ForeignKey('agency_company.id'),index=True,nullable=True)
    name=db.Column(db.String(160),nullable=False,index=True); code=db.Column(db.String(50),index=True)
    phone=db.Column(db.String(40)); email=db.Column(db.String(160)); active=db.Column(db.Boolean,default=True); created_at=db.Column(db.DateTime,default=now)

class Vehicle(db.Model):
    id=db.Column(db.Integer,primary_key=True); agency_id=db.Column(db.Integer,db.ForeignKey('agency_company.id'),index=True,nullable=True)
    account_no=db.Column(db.String(100),index=True); loan_no=db.Column(db.String(100),index=True)
    registration_no=db.Column(db.String(100),index=True); registration_suffix=db.Column(db.String(4),index=True); chassis_no=db.Column(db.String(150),index=True); engine_no=db.Column(db.String(150),index=True)
    customer_name=db.Column(db.String(200),index=True); mobile=db.Column(db.String(40),index=True); city=db.Column(db.String(100),index=True); state=db.Column(db.String(100)); pincode=db.Column(db.String(20))
    vehicle_type=db.Column(db.String(50),index=True); bank_id=db.Column(db.Integer,db.ForeignKey('bank.id'),index=True); source_file_id=db.Column(db.Integer,db.ForeignKey('app_file.id'),index=True)
    latitude=db.Column(db.Float); longitude=db.Column(db.Float); status=db.Column(db.String(30),default='PENDING',index=True); version=db.Column(db.Integer,default=1,index=True)
    updated_at=db.Column(db.DateTime,default=now,onupdate=now); created_at=db.Column(db.DateTime,default=now)

class DataAssignment(db.Model):
    id=db.Column(db.Integer,primary_key=True); agency_id=db.Column(db.Integer,db.ForeignKey('agency_company.id'),index=True,nullable=True)
    user_id=db.Column(db.Integer,db.ForeignKey('user.id'),nullable=False,index=True); bank_id=db.Column(db.Integer,db.ForeignKey('bank.id')); vehicle_id=db.Column(db.Integer,db.ForeignKey('vehicle.id'))
    permission=db.Column(db.String(30),default='READ'); active=db.Column(db.Boolean,default=True); created_at=db.Column(db.DateTime,default=now)
class StatusHistory(db.Model):
    # Compatibility history table. The existing Supabase public.status_history is UUID/recovery-case based and is preserved untouched.
    __tablename__='status_history_legacy'
    id=db.Column(db.Integer,primary_key=True); agency_id=db.Column(db.Integer,db.ForeignKey('agency_company.id'),index=True,nullable=True)
    vehicle_id=db.Column(db.Integer,db.ForeignKey('vehicle.id'),nullable=False,index=True); user_id=db.Column(db.Integer,db.ForeignKey('user.id'),nullable=False)
    old_status=db.Column(db.String(30)); new_status=db.Column(db.String(30),nullable=False); source=db.Column(db.String(20),default='WEB'); created_at=db.Column(db.DateTime,default=now)
class Notification(db.Model):
    id=db.Column(db.Integer,primary_key=True); agency_id=db.Column(db.Integer,db.ForeignKey('agency_company.id'),index=True,nullable=True)
    user_id=db.Column(db.Integer,db.ForeignKey('user.id'),nullable=False,index=True); title=db.Column(db.String(200),nullable=False); message=db.Column(db.Text,nullable=False); type=db.Column(db.String(40),default='INFO'); read_at=db.Column(db.DateTime); created_at=db.Column(db.DateTime,default=now)
class SyncMutation(db.Model):
    id=db.Column(db.String(64),primary_key=True); agency_id=db.Column(db.Integer,db.ForeignKey('agency_company.id'),index=True,nullable=True)
    user_id=db.Column(db.Integer,db.ForeignKey('user.id'),nullable=False); vehicle_id=db.Column(db.Integer,db.ForeignKey('vehicle.id'),nullable=False); new_status=db.Column(db.String(30),nullable=False); created_at=db.Column(db.DateTime,default=now); applied_at=db.Column(db.DateTime)
class Client(db.Model):
    id=db.Column(db.Integer,primary_key=True); agency_id=db.Column(db.Integer,db.ForeignKey('agency_company.id'),index=True,nullable=True)
    name=db.Column(db.String(200),nullable=False,index=True); code=db.Column(db.String(80),index=True); phone=db.Column(db.String(40),index=True); email=db.Column(db.String(160),index=True); active=db.Column(db.Boolean,default=True,nullable=False); created_at=db.Column(db.DateTime,default=now)
class RecoveryCase(db.Model):
    id=db.Column(db.Integer,primary_key=True); agency_id=db.Column(db.Integer,db.ForeignKey('agency_company.id'),index=True,nullable=True)
    vehicle_id=db.Column(db.Integer,db.ForeignKey('vehicle.id'),nullable=False,index=True); client_id=db.Column(db.Integer,db.ForeignKey('client.id'),index=True); assigned_user_id=db.Column(db.Integer,db.ForeignKey('user.id'),index=True); stage=db.Column(db.String(40),default='OPEN',index=True); notes=db.Column(db.Text); next_action_at=db.Column(db.DateTime); created_at=db.Column(db.DateTime,default=now); updated_at=db.Column(db.DateTime,default=now,onupdate=now)
class AppFile(db.Model):
    id=db.Column(db.Integer,primary_key=True); agency_id=db.Column(db.Integer,db.ForeignKey('agency_company.id'),index=True,nullable=True)
    filename=db.Column(db.String(255),nullable=False); vehicle_type=db.Column(db.String(40),index=True); bank_id=db.Column(db.Integer,db.ForeignKey('bank.id'),index=True); record_count=db.Column(db.Integer,default=0); status=db.Column(db.String(40),default='IMPORTED'); uploaded_by=db.Column(db.Integer,db.ForeignKey('user.id')); created_at=db.Column(db.DateTime,default=now)
class AgencyCompany(db.Model):
    id=db.Column(db.Integer,primary_key=True); name=db.Column(db.String(200),nullable=False,unique=True); code=db.Column(db.String(80),unique=True); phone=db.Column(db.String(40)); email=db.Column(db.String(160)); active=db.Column(db.Boolean,default=True); supabase_agency_id=db.Column(UUIDString(),unique=True,index=True); created_at=db.Column(db.DateTime,default=now)
class Challan(db.Model):
    id=db.Column(db.Integer,primary_key=True); agency_id=db.Column(db.Integer,db.ForeignKey('agency_company.id'),index=True,nullable=True)
    vehicle_id=db.Column(db.Integer,db.ForeignKey('vehicle.id'),index=True); challan_no=db.Column(db.String(120),index=True); amount=db.Column(db.Numeric(12,2),default=0); status=db.Column(db.String(30),default='PENDING',index=True); due_date=db.Column(db.DateTime); payment_ref=db.Column(db.String(160)); created_at=db.Column(db.DateTime,default=now)
class ChatConversation(db.Model):
    id=db.Column(db.Integer,primary_key=True); agency_id=db.Column(db.Integer,db.ForeignKey('agency_company.id'),index=True,nullable=True)
    subject=db.Column(db.String(200)); created_by=db.Column(db.Integer,db.ForeignKey('user.id')); created_at=db.Column(db.DateTime,default=now)
class ChatMessage(db.Model):
    id=db.Column(db.Integer,primary_key=True); agency_id=db.Column(db.Integer,db.ForeignKey('agency_company.id'),index=True,nullable=True)
    conversation_id=db.Column(db.Integer,db.ForeignKey('chat_conversation.id'),nullable=False,index=True); sender_id=db.Column(db.Integer,db.ForeignKey('user.id'),nullable=False); message=db.Column(db.Text,nullable=False); created_at=db.Column(db.DateTime,default=now)
class Report(db.Model):
    id=db.Column(db.Integer,primary_key=True); agency_id=db.Column(db.Integer,db.ForeignKey('agency_company.id'),index=True,nullable=True)
    name=db.Column(db.String(200),nullable=False); report_type=db.Column(db.String(60),nullable=False); status=db.Column(db.String(30),default='READY'); created_by=db.Column(db.Integer,db.ForeignKey('user.id')); payload=db.Column(db.Text); created_at=db.Column(db.DateTime,default=now)

TENANT_MODELS=(User,UserOTP,Bank,Vehicle,DataAssignment,StatusHistory,Notification,SyncMutation,Client,RecoveryCase,AppFile,Challan,ChatConversation,ChatMessage,Report)
