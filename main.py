"""
Real Marketing 5.0 Mini App - Backend v2
FastAPI + PostgreSQL
"""
import os
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text
from dotenv import load_dotenv

# .env faylni o'qish
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True, pool_recycle=300, pool_size=5, max_overflow=10)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


app = FastAPI(
    title="RM5.0 Mini App API",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


# ============== HEALTH ==============

@app.get("/")
async def root():
    return {
        "status": "ok",
        "message": "🚀 RM5.0 Mini App API ishlamoqda!",
        "version": "2.0.0",
    }


@app.get("/health")
async def health():
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(text("SELECT 1"))
            result.fetchone()
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB xato: {str(e)}")


@app.get("/api/auth/me")
async def get_auth_me(telegram_id: int, session: AsyncSession = Depends(get_db)):
    """Telegram ID asosida user va role qaytaradi"""
    try:
        user_row = (await session.execute(
            text("""
                SELECT u.id, u.telegram_id, u.full_name, u.username, u.role, u.status,
                       u.group_id, g.name AS group_name,
                       curator.full_name AS curator_name
                FROM users u
                LEFT JOIN groups g ON g.id = u.group_id
                LEFT JOIN users curator ON curator.id = g.curator_id
                WHERE u.telegram_id = :tg_id
            """),
            {"tg_id": telegram_id},
        )).fetchone()
        
        if not user_row:
            raise HTTPException(status_code=404, detail="O'quvchi topilmadi")
        
        return {
            "user_id": user_row.id,
            "telegram_id": user_row.telegram_id,
            "full_name": user_row.full_name,
            "username": user_row.username,
            "role": user_row.role,
            "status": user_row.status,
            "group_id": user_row.group_id,
            "group_name": user_row.group_name,
            "curator_name": user_row.curator_name,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Xato: {str(e)}")


@app.get("/api/stats")
async def get_stats(session: AsyncSession = Depends(get_db)):
    try:
        students_count = (await session.execute(
            text("SELECT COUNT(*) FROM users WHERE role = 'STUDENT' AND status = 'APPROVED'")
        )).scalar() or 0
        
        curators_count = (await session.execute(
            text("SELECT COUNT(*) FROM users WHERE role = 'CURATOR' AND status = 'APPROVED'")
        )).scalar() or 0
        
        lessons_count = (await session.execute(text("SELECT COUNT(*) FROM lessons"))).scalar() or 0
        
        submissions_24h = (await session.execute(
            text("SELECT COUNT(*) FROM submissions WHERE submitted_at >= NOW() - INTERVAL '24 hours'")
        )).scalar() or 0

        return {
            "students": students_count,
            "curators": curators_count,
            "lessons": lessons_count,
            "submissions_24h": submissions_24h,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Xato: {str(e)}")


# ============== STUDENT ENDPOINTS ==============

@app.get("/api/student/{telegram_id}/profile")
async def get_student_profile(telegram_id: int, session: AsyncSession = Depends(get_db)):
    try:
        result = await session.execute(
            text("""
                SELECT u.id, u.full_name, u.username, u.phone, u.telegram_id,
                       g.name AS group_name, c.full_name AS curator_name
                FROM users u
                LEFT JOIN groups g ON g.id = u.group_id
                LEFT JOIN users c ON c.id = g.curator_id
                WHERE u.telegram_id = :tg_id
                  AND u.role = 'STUDENT'
                  AND u.status = 'APPROVED'
            """),
            {"tg_id": telegram_id},
        )
        row = result.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="O'quvchi topilmadi")

        return {
            "id": row.id,
            "full_name": row.full_name,
            "username": row.username,
            "phone": row.phone,
            "telegram_id": row.telegram_id,
            "group_name": row.group_name,
            "curator_name": row.curator_name,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Xato: {str(e)}")


@app.get("/api/student/{telegram_id}/scores")
async def get_student_scores(telegram_id: int, session: AsyncSession = Depends(get_db)):
    try:
        user_row = (await session.execute(
            text("SELECT id FROM users WHERE telegram_id = :tg_id"),
            {"tg_id": telegram_id},
        )).fetchone()
        
        if not user_row:
            raise HTTPException(status_code=404, detail="O'quvchi topilmadi")
        
        user_id = user_row.id

        scores_result = await session.execute(
            text("""
                SELECT type, COALESCE(SUM(score), 0) AS total
                FROM submissions
                WHERE user_id = :uid AND status = 'APPROVED'
                GROUP BY type
            """),
            {"uid": user_id},
        )
        scores_by_type = {row.type: row.total for row in scores_result.fetchall()}

        test_total = (await session.execute(
            text("SELECT COALESCE(SUM(score), 0) AS total FROM test_scores WHERE user_id = :uid"),
            {"uid": user_id},
        )).scalar() or 0

        workshop_total = (await session.execute(
            text("SELECT COALESCE(SUM(score), 0) AS total FROM workshop_scores WHERE user_id = :uid"),
            {"uid": user_id},
        )).scalar() or 0

        ig_total = (await session.execute(
            text("SELECT COALESCE(SUM(week_total), 0) AS total FROM instagram_weeks WHERE user_id = :uid"),
            {"uid": user_id},
        )).scalar() or 0

        konspekt = scores_by_type.get("KONSPEKT", 0)
        workbook = scores_by_type.get("WORKBOOK", 0)
        amaliy = scores_by_type.get("AMALIY", 0)

        total = konspekt + workbook + amaliy + test_total + workshop_total + ig_total

        return {
            "konspekt": konspekt,
            "workbook": workbook,
            "amaliy": amaliy,
            "test": test_total,
            "workshop": workshop_total,
            "instagram": ig_total,
            "total": total,
            "max_total": 1970,
            "percentage": round((total / 1970) * 100, 1),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Xato: {str(e)}")


@app.get("/api/student/{telegram_id}/lessons")
async def get_student_lessons(telegram_id: int, session: AsyncSession = Depends(get_db)):
    """Har bir dars uchun o'quvchi holati"""
    try:
        user_row = (await session.execute(
            text("SELECT id FROM users WHERE telegram_id = :tg_id"),
            {"tg_id": telegram_id},
        )).fetchone()
        
        if not user_row:
            raise HTTPException(status_code=404, detail="O'quvchi topilmadi")
        
        user_id = user_row.id

        # Hamma darslar
        lessons_result = await session.execute(
            text("""
                SELECT id, lesson_number, title, lesson_date, 
                       is_unlocked, speaker,
                       workbook_file_id IS NOT NULL AS has_workbook,
                       has_practical
                FROM lessons
                ORDER BY lesson_number
            """)
        )
        lessons = lessons_result.fetchall()

        result = []
        for lesson in lessons:
            # Har dars uchun submissions
            subs_result = await session.execute(
                text("""
                    SELECT type, status, score
                    FROM submissions
                    WHERE user_id = :uid AND lesson_id = :lid
                """),
                {"uid": user_id, "lid": lesson.id},
            )
            subs_by_type = {row.type: {"status": row.status, "score": row.score} for row in subs_result.fetchall()}

            # Test
            test_row = (await session.execute(
                text("SELECT score FROM test_scores WHERE user_id = :uid AND lesson_id = :lid"),
                {"uid": user_id, "lid": lesson.id},
            )).fetchone()

            result.append({
                "lesson_id": lesson.id,
                "lesson_number": lesson.lesson_number,
                "title": lesson.title,
                "lesson_date": lesson.lesson_date.isoformat() if lesson.lesson_date else None,
                "is_unlocked": lesson.is_unlocked,
                "speaker": lesson.speaker,
                "konspekt": subs_by_type.get("KONSPEKT"),
                "workbook": subs_by_type.get("WORKBOOK") if lesson.has_workbook else None,
                "amaliy": subs_by_type.get("AMALIY") if lesson.has_practical else None,
                "test": {"score": test_row.score} if test_row else None,
            })

        return {"lessons": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Xato: {str(e)}")


@app.get("/api/student/{telegram_id}/lesson/{lesson_id}")
async def get_lesson_details(telegram_id: int, lesson_id: int, session: AsyncSession = Depends(get_db)):
    """Bitta dars batafsil ma'lumotlari"""
    try:
        user_row = (await session.execute(
            text("SELECT id, full_name FROM users WHERE telegram_id = :tg_id"),
            {"tg_id": telegram_id},
        )).fetchone()
        
        if not user_row:
            raise HTTPException(status_code=404, detail="O'quvchi topilmadi")
        
        user_id = user_row.id

        # Dars ma'lumotlari
        lesson_row = (await session.execute(
            text("""
                SELECT id, lesson_number, title, lesson_date, is_unlocked, speaker,
                       workbook_file_id, workbook_deadline,
                       has_practical
                FROM lessons
                WHERE id = :lid
            """),
            {"lid": lesson_id},
        )).fetchone()
        
        if not lesson_row:
            raise HTTPException(status_code=404, detail="Dars topilmadi")

        # Submissionlar to'liq (feedback, reviewer, sana bilan)
        subs_result = await session.execute(
            text("""
                SELECT s.type, s.status, s.score, s.max_score, s.feedback,
                       s.submitted_at, s.reviewed_at, s.is_late,
                       r.full_name AS reviewer_name
                FROM submissions s
                LEFT JOIN users r ON r.id = s.reviewer_id
                WHERE s.user_id = :uid AND s.lesson_id = :lid
                ORDER BY s.submitted_at DESC
            """),
            {"uid": user_id, "lid": lesson_id},
        )
        
        subs_by_type = {}
        for row in subs_result.fetchall():
            t = row.type
            if t not in subs_by_type:
                subs_by_type[t] = {
                    "status": row.status,
                    "score": row.score or 0,
                    "max_score": row.max_score or 0,
                    "feedback": row.feedback,
                    "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
                    "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
                    "is_late": row.is_late or False,
                    "reviewer_name": row.reviewer_name,
                }

        # Test score
        test_row = (await session.execute(
            text("SELECT score FROM test_scores WHERE user_id = :uid AND lesson_id = :lid"),
            {"uid": user_id, "lid": lesson_id},
        )).fetchone()
        
        # Jami ball (shu dars uchun)
        konspekt_score = (subs_by_type.get("KONSPEKT") or {}).get("score", 0)
        workbook_score = (subs_by_type.get("WORKBOOK") or {}).get("score", 0)
        amaliy_score = (subs_by_type.get("AMALIY") or {}).get("score", 0)
        test_score = test_row.score if test_row else 0
        total_earned = konspekt_score + workbook_score + amaliy_score + test_score
        
        # Max ball (shu dars uchun)
        max_total = 10  # Konspekt
        if lesson_row.workbook_file_id:
            max_total += 20  # Workbook
        if lesson_row.has_practical:
            max_total += 50  # Amaliy
        max_total += 20  # Test

        return {
            "lesson_id": lesson_row.id,
            "lesson_number": lesson_row.lesson_number,
            "title": lesson_row.title,
            "lesson_date": lesson_row.lesson_date.isoformat() if lesson_row.lesson_date else None,
            "is_unlocked": lesson_row.is_unlocked,
            "speaker": lesson_row.speaker,
            "has_workbook": lesson_row.workbook_file_id is not None,
            "has_practical": lesson_row.has_practical,
            "workbook_deadline": lesson_row.workbook_deadline.isoformat() if lesson_row.workbook_deadline else None,
            "total_earned": total_earned,
            "max_total": max_total,
            "submissions": {
                "konspekt": subs_by_type.get("KONSPEKT"),
                "workbook": subs_by_type.get("WORKBOOK") if lesson_row.workbook_file_id else None,
                "amaliy": subs_by_type.get("AMALIY") if lesson_row.has_practical else None,
                "test": {"score": test_row.score} if test_row else None,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Xato: {str(e)}")


@app.get("/api/student/{telegram_id}/dashboard")
async def get_student_dashboard(telegram_id: int, session: AsyncSession = Depends(get_db)):
    """Bosh sahifa uchun barcha qo'shimcha ma'lumotlar"""
    try:
        user_row = (await session.execute(
            text("SELECT id FROM users WHERE telegram_id = :tg_id"),
            {"tg_id": telegram_id},
        )).fetchone()
        
        if not user_row:
            raise HTTPException(status_code=404, detail="O'quvchi topilmadi")
        
        user_id = user_row.id

        # 1. Upcoming lesson - keyingi unlocked dars (yoki bugundan keyingi)
        upcoming_lesson = (await session.execute(
            text("""
                SELECT id, lesson_number, title, lesson_date, speaker
                FROM lessons
                WHERE lesson_date > NOW()
                ORDER BY lesson_date ASC
                LIMIT 1
            """)
        )).fetchone()
        
        upcoming = None
        if upcoming_lesson:
            upcoming = {
                "lesson_id": upcoming_lesson.id,
                "lesson_number": upcoming_lesson.lesson_number,
                "title": upcoming_lesson.title,
                "lesson_date": upcoming_lesson.lesson_date.isoformat() if upcoming_lesson.lesson_date else None,
                "speaker": upcoming_lesson.speaker,
            }

        # 2. Recent activity - oxirgi 5 ta submission (APPROVED status uchun)
        recent_subs = await session.execute(
            text("""
                SELECT s.type, s.status, s.score, s.reviewed_at,
                       l.lesson_number, l.title AS lesson_title
                FROM submissions s
                LEFT JOIN lessons l ON l.id = s.lesson_id
                WHERE s.user_id = :uid
                ORDER BY COALESCE(s.reviewed_at, s.submitted_at) DESC
                LIMIT 5
            """),
            {"uid": user_id},
        )
        
        recent_activity = []
        for row in recent_subs.fetchall():
            recent_activity.append({
                "type": row.type,
                "status": row.status,
                "score": row.score or 0,
                "lesson_number": row.lesson_number,
                "lesson_title": row.lesson_title,
                "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
            })

        # 3. Weekly activity - oxirgi 7 kun uchun story bormi
        weekly = await session.execute(
            text("""
                SELECT DATE(report_date) AS day, COUNT(*) AS cnt
                FROM story_reports
                WHERE user_id = :uid AND report_date >= NOW() - INTERVAL '7 days'
                GROUP BY DATE(report_date)
            """),
            {"uid": user_id},
        )
        
        days_with_story = set()
        for row in weekly.fetchall():
            days_with_story.add(str(row.day))
        
        from datetime import datetime, timedelta
        today = datetime.now().date()
        weekly_activity = []
        day_labels = ["D", "S", "CH", "P", "J", "SH", "Y"]  # Dush, Sesh, Chor, Pay, Jum, Shan, Yak
        # Pythonda weekday(): 0=Dushanba, 6=Yakshanba
        for i in range(7):
            day = today - timedelta(days=6 - i)
            weekly_activity.append({
                "label": day_labels[day.weekday()],
                "date": day.isoformat(),
                "active": str(day) in days_with_story,
            })
        active_days_count = sum(1 for d in weekly_activity if d["active"])

        # 4. Attendance summary
        att = (await session.execute(
            text("""
                SELECT 
                    SUM(CASE WHEN status = 'ON_TIME' THEN 1 ELSE 0 END) AS present,
                    SUM(CASE WHEN status IN ('LATE_TIER_1', 'LATE_TIER_2', 'LATE_TIER_3') THEN 1 ELSE 0 END) AS late,
                    SUM(CASE WHEN status = 'ABSENT' THEN 1 ELSE 0 END) AS absent,
                    SUM(CASE WHEN status = 'EXCUSED' THEN 1 ELSE 0 END) AS excused
                FROM lesson_attendance
                WHERE user_id = :uid
            """),
            {"uid": user_id},
        )).fetchone()
        
        attendance = {
            "present": int(att.present or 0),
            "late": int(att.late or 0),
            "absent": int(att.absent or 0),
            "excused": int(att.excused or 0),
        }

        # 5. Fines
        fines_row = (await session.execute(
            text("""
                SELECT 
                    COALESCE(SUM(CASE WHEN status = 'UNPAID' THEN amount_uzs ELSE 0 END), 0) AS unpaid,
                    COALESCE(SUM(CASE WHEN status = 'PAID' THEN amount_uzs ELSE 0 END), 0) AS paid
                FROM fines
                WHERE user_id = :uid
            """),
            {"uid": user_id},
        )).fetchone()
        
        fines = {
            "unpaid_uzs": int(fines_row.unpaid or 0),
            "paid_uzs": int(fines_row.paid or 0),
        }

        return {
            "upcoming_lesson": upcoming,
            "recent_activity": recent_activity,
            "weekly_activity": weekly_activity,
            "active_days_count": active_days_count,
            "attendance": attendance,
            "fines": fines,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Xato: {str(e)}")


@app.get("/api/student/{telegram_id}/ranking")
async def get_student_ranking(telegram_id: int, session: AsyncSession = Depends(get_db)):
    """O'quvchi reytingi (guruh va kursda)"""
    try:
        user_row = (await session.execute(
            text("SELECT id, group_id FROM users WHERE telegram_id = :tg_id"),
            {"tg_id": telegram_id},
        )).fetchone()
        
        if not user_row:
            raise HTTPException(status_code=404, detail="O'quvchi topilmadi")
        
        user_id = user_row.id
        group_id = user_row.group_id

        # Hamma o'quvchilar ballari
        all_scores = await session.execute(
            text("""
                SELECT 
                    u.id, u.full_name, u.group_id,
                    g.name AS group_name,
                    COALESCE(s.subs_total, 0) + 
                    COALESCE(t.test_total, 0) + 
                    COALESCE(w.workshop_total, 0) + 
                    COALESCE(sr.story_total, 0) +
                    COALESCE(b.bonus_total, 0) AS total
                FROM users u
                LEFT JOIN groups g ON g.id = u.group_id
                LEFT JOIN (
                    SELECT user_id, SUM(score) AS subs_total 
                    FROM submissions WHERE status = 'APPROVED' 
                    GROUP BY user_id
                ) s ON s.user_id = u.id
                LEFT JOIN (
                    SELECT user_id, SUM(score) AS test_total 
                    FROM test_scores 
                    GROUP BY user_id
                ) t ON t.user_id = u.id
                LEFT JOIN (
                    SELECT user_id, SUM(score) AS workshop_total 
                    FROM workshop_scores 
                    GROUP BY user_id
                ) w ON w.user_id = u.id
                LEFT JOIN (
                    SELECT user_id, SUM(score) AS story_total 
                    FROM story_reports WHERE status = 'APPROVED'
                    GROUP BY user_id
                ) sr ON sr.user_id = u.id
                LEFT JOIN (
                    SELECT user_id, SUM(amount) AS bonus_total 
                    FROM bonus_points
                    GROUP BY user_id
                ) b ON b.user_id = u.id
                WHERE u.role = 'STUDENT' AND u.status = 'APPROVED'
                ORDER BY total DESC
            """)
        )
        all_students = all_scores.fetchall()

        # Course ranking
        course_position = None
        for i, s in enumerate(all_students, 1):
            if s.id == user_id:
                course_position = i
                break

        # Group ranking
        group_students = [s for s in all_students if s.group_id == group_id]
        group_position = None
        for i, s in enumerate(group_students, 1):
            if s.id == user_id:
                group_position = i
                break

        # TOP 10
        top10 = [
            {
                "position": i,
                "full_name": s.full_name,
                "group_name": s.group_name,
                "total": int(s.total),
                "is_me": s.id == user_id,
            }
            for i, s in enumerate(all_students[:10], 1)
        ]

        return {
            "course_position": course_position,
            "course_total": len(all_students),
            "group_position": group_position,
            "group_total": len(group_students),
            "top10": top10,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Xato: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
