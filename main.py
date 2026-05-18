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
engine = create_async_engine(DATABASE_URL, echo=False)
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
                    COALESCE(ig.ig_total, 0) AS total
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
                    SELECT user_id, SUM(week_total) AS ig_total 
                    FROM instagram_weeks 
                    GROUP BY user_id
                ) ig ON ig.user_id = u.id
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
