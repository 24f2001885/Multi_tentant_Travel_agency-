"""
Platform monitoring service for business and infrastructure load (TDR v2 §4.6; Milestone P13).
"""

from collections import deque
from datetime import datetime, timezone
import os
import shutil
import threading
import time
from typing import Any, Dict, List

from flask import current_app
from sqlalchemy import func, select, text

from app.extensions import db, scheduler
from app.models.booking import Booking
from app.models.organization import Organization
from app.models.payment import Payment
from app.models.ticket import Ticket
from app.models.trek import Trek
from app.models.user import User
from app.tenancy.scoping import tenant_bypass


class RequestTelemetry:
    """Thread-safe in-memory rolling HTTP request and latency telemetry tracker."""

    def __init__(self):
        self.lock = threading.Lock()
        self.start_time = datetime.now(timezone.utc)
        self.total_requests = 0
        self.status_2xx = 0
        self.status_3xx = 0
        self.status_4xx = 0
        self.status_5xx = 0
        self.cumulative_latency_ms = 0.0
        self.recent_requests = deque(maxlen=20)

    def record(self, method: str, host: str, path: str, status_code: int, duration_ms: float):
        """Records an HTTP request outcome."""
        if path.startswith("/static/"):
            return

        with self.lock:
            self.total_requests += 1
            if 200 <= status_code < 300:
                self.status_2xx += 1
            elif 300 <= status_code < 400:
                self.status_3xx += 1
            elif 400 <= status_code < 500:
                self.status_4xx += 1
            elif status_code >= 500:
                self.status_5xx += 1

            self.cumulative_latency_ms += duration_ms
            now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
            clean_host = (host or "").split(":")[0]

            self.recent_requests.appendleft({
                "time": now_str,
                "method": method,
                "host": clean_host,
                "path": path,
                "status_code": status_code,
                "latency_ms": round(duration_ms, 1),
            })

    def get_summary(self) -> Dict[str, Any]:
        with self.lock:
            avg_lat = (
                round(self.cumulative_latency_ms / self.total_requests, 1)
                if self.total_requests > 0
                else 0.0
            )
            uptime_seconds = int((datetime.now(timezone.utc) - self.start_time).total_seconds())
            hours, remainder = divmod(uptime_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime_str = f"{hours}h {minutes}m {seconds}s" if hours > 0 else f"{minutes}m {seconds}s"

            return {
                "total_requests": self.total_requests,
                "status_2xx": self.status_2xx,
                "status_3xx": self.status_3xx,
                "status_4xx": self.status_4xx,
                "status_5xx": self.status_5xx,
                "avg_latency_ms": avg_lat,
                "uptime": uptime_str,
                "recent_requests": list(self.recent_requests),
            }


telemetry = RequestTelemetry()


def _get_ram_metrics() -> Dict[str, Any]:
    """Inspects Linux /proc/meminfo or system memory stats."""
    total_mb = 0.0
    used_mb = 0.0
    avail_mb = 0.0
    pct = 0.0

    if os.path.exists("/proc/meminfo"):
        try:
            mem = {}
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        k = parts[0].strip()
                        v = parts[1].strip().split()[0]
                        mem[k] = int(v)
            total_mb = round(mem.get("MemTotal", 0) / 1024.0, 1)
            avail_mb = round(mem.get("MemAvailable", mem.get("MemFree", 0)) / 1024.0, 1)
            used_mb = max(0.0, round(total_mb - avail_mb, 1))
            pct = round((used_mb / total_mb) * 100.0, 1) if total_mb > 0 else 0.0
        except Exception:
            pass

    # Process RSS Memory
    process_rss_mb = 0.0
    try:
        import resource
        process_rss_mb = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
    except Exception:
        pass

    return {
        "total_mb": total_mb,
        "used_mb": used_mb,
        "available_mb": avail_mb,
        "percent_used": pct,
        "process_rss_mb": process_rss_mb,
    }


def _get_cpu_metrics() -> Dict[str, Any]:
    """Inspects system CPU cores and load averages."""
    load_avg = (0.0, 0.0, 0.0)
    try:
        if hasattr(os, "getloadavg"):
            raw = os.getloadavg()
            load_avg = (round(raw[0], 2), round(raw[1], 2), round(raw[2], 2))
    except Exception:
        pass

    cores = os.cpu_count() or 1
    return {
        "cores": cores,
        "load_1m": load_avg[0],
        "load_5m": load_avg[1],
        "load_15m": load_avg[2],
    }


def get_platform_business_metrics() -> Dict[str, Any]:
    """
    Computes platform-wide business metrics across all organizations.
    Always runs in tenant_bypass() for Platform Admin consumption.
    """
    with tenant_bypass():
        total_orgs = Organization.query.count()
        active_orgs = Organization.query.filter_by(status="active").count()
        suspended_orgs = Organization.query.filter_by(status="suspended").count()

        total_users = User.query.count()
        total_treks = Trek.query.count()
        total_bookings = Booking.query.count()

        # Gross Merchandise Value (GMV)
        gmv_res = db.session.query(func.coalesce(func.sum(Booking.total_price), 0)).filter(
            Booking.status.in_(["confirmed", "completed"])
        ).scalar()
        gmv = float(gmv_res) if gmv_res is not None else 0.0

        # Estimated platform commissions (e.g. from commission rate)
        commission_rate = float(os.environ.get("RAZORPAY_PLATFORM_COMMISSION_PERCENT", "5.0") or "5.0")
        estimated_commission = round(gmv * (commission_rate / 100.0), 2)

        # Per-tenant breakdown
        tenants_summary: List[Dict[str, Any]] = []
        orgs = Organization.query.order_by(Organization.created_at.desc()).all()
        for org in orgs:
            b_count = Booking.query.filter_by(organization_id=org.id).count()
            u_count = User.query.filter_by(organization_id=org.id).count()
            t_rev = db.session.query(func.coalesce(func.sum(Booking.total_price), 0)).filter(
                Booking.organization_id == org.id,
                Booking.status.in_(["confirmed", "completed"])
            ).scalar()
            tenants_summary.append({
                "id": str(org.id),
                "name": org.name,
                "status": org.status,
                "users_count": u_count,
                "bookings_count": b_count,
                "revenue": float(t_rev) if t_rev else 0.0,
            })

        return {
            "total_organizations": total_orgs,
            "active_organizations": active_orgs,
            "suspended_organizations": suspended_orgs,
            "total_users": total_users,
            "total_treks": total_treks,
            "total_bookings": total_bookings,
            "gross_merchandise_value": gmv,
            "estimated_commission": estimated_commission,
            "commission_rate_percent": commission_rate,
            "tenants": tenants_summary,
        }


def get_platform_infra_metrics() -> Dict[str, Any]:
    """
    Computes system and infrastructure health metrics.
    """
    backup_root = os.path.join(current_app.instance_path, "backups")
    total_backup_bytes = 0
    if os.path.exists(backup_root):
        for dirpath, _, filenames in os.walk(backup_root):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                total_backup_bytes += os.path.getsize(fp)

    # Disk usage of instance directory
    disk_usage = shutil.disk_usage(current_app.instance_path)

    # Background scheduler job count
    scheduled_jobs = []
    try:
        jobs = scheduler.get_jobs()
        for j in jobs:
            scheduled_jobs.append({
                "id": j.id,
                "next_run_time": j.next_run_time.isoformat() if j.next_run_time else None,
            })
    except Exception:
        pass

    # Database table counts
    with tenant_bypass():
        table_counts = {
            "users": User.query.count(),
            "treks": Trek.query.count(),
            "bookings": Booking.query.count(),
            "tickets": Ticket.query.count(),
        }

    # Detailed Disk Metrics
    disk_total_gb = round(disk_usage.total / (1024**3), 1)
    disk_used_gb = round(disk_usage.used / (1024**3), 1)
    disk_free_gb = round(disk_usage.free / (1024**3), 1)
    disk_pct = round((disk_usage.used / disk_usage.total) * 100.0, 1) if disk_usage.total > 0 else 0.0

    # System RAM & CPU
    ram_metrics = _get_ram_metrics()
    cpu_metrics = _get_cpu_metrics()
    request_telemetry = telemetry.get_summary()

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "database_uri_scheme": "postgresql" if "postgresql" in current_app.config.get("SQLALCHEMY_DATABASE_URI", "") else "sqlite",
        "backup_storage_bytes": total_backup_bytes,
        "disk_free_bytes": disk_usage.free,
        "disk_total_bytes": disk_usage.total,
        "disk_used_bytes": disk_usage.used,
        "disk_total_gb": disk_total_gb,
        "disk_used_gb": disk_used_gb,
        "disk_free_gb": disk_free_gb,
        "disk_percent": disk_pct,
        "ram": ram_metrics,
        "cpu": cpu_metrics,
        "requests": request_telemetry,
        "active_scheduler_jobs": scheduled_jobs,
        "table_counts": table_counts,
    }
