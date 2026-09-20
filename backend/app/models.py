from datetime import datetime, timezone
from . import db

def now(): return datetime.now(timezone.utc)

class User(db.Model):
    __tablename__='users'
    id=db.Column(db.BigInteger,primary_key=True); username=db.Column(db.Text,unique=True,nullable=False,index=True); password_hash=db.Column(db.Text,nullable=False); full_name=db.Column(db.Text,nullable=False); phone=db.Column(db.Text,unique=True); email=db.Column(db.Text,unique=True); active=db.Column(db.Boolean,default=True,nullable=False); created_at=db.Column(db.DateTime(timezone=True),default=now)
    @property
    def agency_id(self): return 1
    @property
    def is_deleted(self): return False
    @property
    def device_id(self): return None

class Bank(db.Model):
    __tablename__='banks'
    id=db.Column(db.BigInteger,primary_key=True); name=db.Column(db.Text,nullable=False,unique=True); code=db.Column(db.Text); phone=db.Column(db.Text); email=db.Column(db.Text); active=db.Column(db.Boolean,default=True,nullable=False); created_at=db.Column(db.DateTime(timezone=True),default=now)
    @property
    def agency_id(self): return 1

class Client(db.Model):
    __tablename__='clients'
    id=db.Column(db.BigInteger,primary_key=True); name=db.Column(db.Text,nullable=False); phone=db.Column(db.Text); email=db.Column(db.Text); address=db.Column(db.Text); created_by=db.Column(db.Text); created_at=db.Column(db.DateTime(timezone=True),default=now)
    @property
    def agency_id(self): return 1

class Vehicle(db.Model):
    __tablename__='vehicles'
    id=db.Column(db.BigInteger,primary_key=True); account_no=db.Column(db.Text,index=True); loan_no=db.Column(db.Text,index=True); registration_no=db.Column(db.Text,index=True); chassis_no=db.Column(db.Text,index=True); engine_no=db.Column(db.Text,index=True); customer_name=db.Column(db.Text,index=True); mobile=db.Column(db.Text,index=True); city=db.Column(db.Text,index=True); state=db.Column(db.Text); pincode=db.Column(db.Text); vehicle_type=db.Column(db.Text); bank_id=db.Column(db.BigInteger,index=True); status=db.Column(db.Text,default='PENDING',index=True); latitude=db.Column(db.Float); longitude=db.Column(db.Float); source_file_name=db.Column(db.Text); version=db.Column(db.Integer,default=1); created_at=db.Column(db.DateTime(timezone=True),default=now); updated_at=db.Column(db.DateTime(timezone=True),default=now)
    @property
    def agency_id(self): return 1
    @property
    def registration_suffix(self):
        import re
        d=re.sub(r'\D','',self.registration_no or '')
        return d[-4:] if len(d)>=4 else None
    @property
    def source_file_id(self): return None

class RecoveryCase(db.Model):
    __tablename__='recovery_cases'
    id=db.Column(db.BigInteger,primary_key=True); vehicle_id=db.Column(db.BigInteger,nullable=False); client_id=db.Column(db.BigInteger); assigned_user_id=db.Column(db.Text); status=db.Column(db.Text,default='pending'); priority=db.Column(db.Text,default='normal'); amount=db.Column(db.Numeric); notes=db.Column(db.Text); created_by=db.Column(db.Text); created_at=db.Column(db.DateTime(timezone=True),default=now); updated_at=db.Column(db.DateTime(timezone=True),default=now)
    @property
    def agency_id(self): return 1
    @property
    def stage(self): return self.status

class StatusHistory(db.Model):
    __tablename__='status_history'
    id=db.Column(db.BigInteger,primary_key=True); recovery_case_id=db.Column(db.Text); old_status=db.Column(db.Text); new_status=db.Column(db.Text,nullable=False); changed_by=db.Column(db.Text); note=db.Column(db.Text); created_at=db.Column(db.DateTime(timezone=True),default=now)
    @property
    def agency_id(self): return 1
    @property
    def vehicle_id(self): return None
    @property
    def user_id(self): return self.changed_by
    @property
    def source(self): return 'WEB'

class AppFile(db.Model):
    __tablename__='app_files'
    id=db.Column(db.BigInteger,primary_key=True); filename=db.Column(db.Text,nullable=False); vehicle_type=db.Column(db.Text); bank_id=db.Column(db.BigInteger); uploaded_by=db.Column(db.Text); record_count=db.Column(db.Integer,default=0); status=db.Column(db.Text,default='IMPORTED'); created_at=db.Column(db.DateTime(timezone=True),default=now)
    @property
    def agency_id(self): return 1
    @property
    def duplicate_count(self): return 0
    @property
    def import_status(self): return self.status

class DataAssignment(db.Model): __tablename__='data_assignment_legacy'; id=db.Column(db.Integer,primary_key=True)
class Notification(db.Model): __tablename__='notification_legacy'; id=db.Column(db.Integer,primary_key=True)
class SyncMutation(db.Model): __tablename__='sync_mutation_legacy'; id=db.Column(db.String(64),primary_key=True)
class AgencyCompany(db.Model): __tablename__='agency_company_legacy'; id=db.Column(db.Integer,primary_key=True)
class Challan(db.Model): __tablename__='challan_legacy'; id=db.Column(db.Integer,primary_key=True)
class ChatConversation(db.Model): __tablename__='chat_conversation_legacy'; id=db.Column(db.Integer,primary_key=True)
class ChatMessage(db.Model): __tablename__='chat_message_legacy'; id=db.Column(db.Integer,primary_key=True)
class Report(db.Model): __tablename__='report_legacy'; id=db.Column(db.Integer,primary_key=True)
class UserOTP(db.Model): __tablename__='user_otp_legacy'; id=db.Column(db.Integer,primary_key=True)

TENANT_MODELS=(Bank,Vehicle,Client,RecoveryCase,StatusHistory,AppFile)
