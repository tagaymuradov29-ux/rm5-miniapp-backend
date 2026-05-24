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


@app.get("/api/admin/dashboard")
async def get_admin_dashboard(session: AsyncSession = Depends(get_db)):
    """Admin bosh sahifa uchun barcha ma'lumotlar"""
    try:
        # 1. Hero stats
        stats_row = (await session.execute(
            text("""
                SELECT 
                    (SELECT COUNT(*) FROM users WHERE role = 'STUDENT' AND status = 'APPROVED') AS total_students,
                    (SELECT COUNT(*) FROM groups) AS total_groups,
                    (SELECT COUNT(*) FROM lessons) AS total_lessons,
                    (SELECT COUNT(*) FROM lessons WHERE is_unlocked = true) AS unlocked_lessons
            """)
        )).fetchone()
        
        # 2. Bugungi holat (pending counts)
        pending_row = (await session.execute(
            text("""
                SELECT 
                    (SELECT COUNT(*) FROM submissions WHERE status = 'PENDING') AS pending_subs,
                    (SELECT COUNT(*) FROM users WHERE status = 'PENDING') AS pending_users,
                    (SELECT COUNT(*) FROM fines WHERE status = 'PROOF_SUBMITTED') AS pending_fines
            """)
        )).fetchone()
        
        # 3. Oxirgi 8 ta harakat (recent activity)
        # APPROVED submissions + new users + paid fines
        recent_subs = await session.execute(
            text("""
                SELECT 
                    'submission_approved' AS type,
                    s.reviewed_at AS event_time,
                    u.full_name AS user_name,
                    s.score AS score,
                    s.type AS sub_type,
                    l.lesson_number AS lesson_num
                FROM submissions s
                JOIN users u ON u.id = s.user_id
                LEFT JOIN lessons l ON l.id = s.lesson_id
                WHERE s.status = 'APPROVED' AND s.reviewed_at IS NOT NULL
                ORDER BY s.reviewed_at DESC
                LIMIT 5
            """)
        )
        
        new_users = await session.execute(
            text("""
                SELECT 
                    'new_user' AS type,
                    approved_at AS event_time,
                    full_name AS user_name,
                    NULL AS score,
                    NULL AS sub_type,
                    NULL AS lesson_num
                FROM users
                WHERE status = 'APPROVED' AND role = 'STUDENT' AND approved_at IS NOT NULL
                ORDER BY approved_at DESC
                LIMIT 3
            """)
        )
        
        # Combine and sort
        recent_activity = []
        for row in recent_subs.fetchall():
            recent_activity.append({
                "type": "submission_approved",
                "time": row.event_time.isoformat() if row.event_time else None,
                "user_name": row.user_name,
                "score": row.score or 0,
                "sub_type": row.sub_type,
                "lesson_num": row.lesson_num,
            })
        
        for row in new_users.fetchall():
            recent_activity.append({
                "type": "new_user",
                "time": row.event_time.isoformat() if row.event_time else None,
                "user_name": row.user_name,
                "score": None,
                "sub_type": None,
                "lesson_num": None,
            })
        
        # Sort by time DESC
        recent_activity.sort(key=lambda x: x["time"] or "", reverse=True)
        recent_activity = recent_activity[:8]

        return {
            "stats": {
                "total_students": int(stats_row.total_students or 0),
                "total_groups": int(stats_row.total_groups or 0),
                "total_lessons": int(stats_row.total_lessons or 0),
                "unlocked_lessons": int(stats_row.unlocked_lessons or 0),
            },
            "pending": {
                "submissions": int(pending_row.pending_subs or 0),
                "users": int(pending_row.pending_users or 0),
                "fines": int(pending_row.pending_fines or 0),
            },
            "recent_activity": recent_activity,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Xato: {str(e)}")


@app.get("/api/admin/trend")
async def get_admin_trend(
    task_type: str = "all",
    group_id: int = None,
    session: AsyncSession = Depends(get_db),
):
    """Trend analytics"""
    try:
        if task_type == "konspekt":
            score_expr = "(SELECT COALESCE(AVG(score), 0) FROM submissions WHERE lesson_id = l.id AND status = 'APPROVED' AND type = 'KONSPEKT'"
            count_expr = "(SELECT COUNT(*) FROM submissions WHERE lesson_id = l.id AND status = 'APPROVED' AND type = 'KONSPEKT'"
            max_score = 10
        elif task_type == "workbook":
            score_expr = "(SELECT COALESCE(AVG(score), 0) FROM submissions WHERE lesson_id = l.id AND status = 'APPROVED' AND type = 'WORKBOOK'"
            count_expr = "(SELECT COUNT(*) FROM submissions WHERE lesson_id = l.id AND status = 'APPROVED' AND type = 'WORKBOOK'"
            max_score = 20
        elif task_type == "amaliy":
            score_expr = "(SELECT COALESCE(AVG(score), 0) FROM submissions WHERE lesson_id = l.id AND status = 'APPROVED' AND type = 'AMALIY'"
            count_expr = "(SELECT COUNT(*) FROM submissions WHERE lesson_id = l.id AND status = 'APPROVED' AND type = 'AMALIY'"
            max_score = 50
        elif task_type == "test":
            score_expr = "(SELECT COALESCE(AVG(score), 0) FROM test_scores WHERE lesson_id = l.id"
            count_expr = "(SELECT COUNT(*) FROM test_scores WHERE lesson_id = l.id"
            max_score = 20
        elif task_type == "workshop":
            score_expr = "(SELECT COALESCE(AVG(score), 0) FROM workshop_scores WHERE lesson_id = l.id"
            count_expr = "(SELECT COUNT(*) FROM workshop_scores WHERE lesson_id = l.id"
            max_score = 50
        elif task_type == "stories":
            score_expr = "(SELECT COALESCE(AVG(score), 0) FROM story_reports WHERE lesson_id = l.id AND status = 'APPROVED'"
            count_expr = "(SELECT COUNT(*) FROM story_reports WHERE lesson_id = l.id AND status = 'APPROVED'"
            max_score = 25
        elif task_type == "reels":
            score_expr = "(SELECT COALESCE(AVG(score), 0) FROM submissions WHERE lesson_id = l.id AND status = 'APPROVED' AND type = 'INSTAGRAM_REELS'"
            count_expr = "(SELECT COUNT(*) FROM submissions WHERE lesson_id = l.id AND status = 'APPROVED' AND type = 'INSTAGRAM_REELS'"
            max_score = 25
        else:
            score_expr = "(SELECT COALESCE(AVG(score), 0) FROM submissions WHERE lesson_id = l.id AND status = 'APPROVED'"
            count_expr = "(SELECT COUNT(*) FROM submissions WHERE lesson_id = l.id AND status = 'APPROVED'"
            max_score = 100
        
        group_filter = ""
        params = {}
        if group_id:
            group_filter = " AND user_id IN (SELECT id FROM users WHERE group_id = :gid)"
            params["gid"] = group_id
        
        score_expr += group_filter + ")"
        count_expr += group_filter + ")"
        
        sql = f"""
            SELECT 
                l.lesson_number,
                l.title,
                l.is_unlocked,
                ROUND({score_expr}::numeric, 1) AS avg_score,
                {count_expr} AS submission_count
            FROM lessons l
            ORDER BY l.lesson_number
        """
        
        rows = (await session.execute(text(sql), params)).fetchall()
        
        lessons = []
        for row in rows:
            lessons.append({
                "lesson_number": row.lesson_number,
                "title": row.title,
                "is_unlocked": row.is_unlocked,
                "avg_score": float(row.avg_score or 0),
                "count": int(row.submission_count or 0),
                "max_score": max_score,
            })
        
        unlocked_with_data = [l for l in lessons if l["is_unlocked"] and l["count"] > 0]
        
        best_lesson = max(unlocked_with_data, key=lambda x: x["avg_score"]) if unlocked_with_data else None
        worst_lesson = min(unlocked_with_data, key=lambda x: x["avg_score"]) if unlocked_with_data else None
        
        biggest_rise = None
        biggest_drop = None
        max_rise_pct = 0
        max_drop_pct = 0
        for i in range(1, len(unlocked_with_data)):
            prev = unlocked_with_data[i-1]
            curr = unlocked_with_data[i]
            if prev["avg_score"] > 0:
                change_pct = ((curr["avg_score"] - prev["avg_score"]) / prev["avg_score"]) * 100
                if change_pct > max_rise_pct:
                    max_rise_pct = change_pct
                    biggest_rise = {"from": prev["lesson_number"], "to": curr["lesson_number"], "percent": round(change_pct, 1)}
                if change_pct < max_drop_pct:
                    max_drop_pct = change_pct
                    biggest_drop = {"from": prev["lesson_number"], "to": curr["lesson_number"], "percent": round(change_pct, 1)}
        
        total_students_sql = "SELECT COUNT(*) AS c FROM users WHERE role = 'STUDENT' AND status = 'APPROVED'"
        if group_id:
            total_students_sql += " AND group_id = :gid"
        total_students = (await session.execute(text(total_students_sql), {"gid": group_id} if group_id else {})).fetchone()
        
        # TOP 3 o'quvchi (bu vazifa bo'yicha)
        top_students_sql_parts = []
        if task_type in ("konspekt", "workbook", "amaliy"):
            sub_type = task_type.upper()
            top_students_sql_parts.append(f"COALESCE((SELECT SUM(score) FROM submissions WHERE user_id = u.id AND status = 'APPROVED' AND type = '{sub_type}'), 0)")
        elif task_type == "reels":
            top_students_sql_parts.append("COALESCE((SELECT SUM(score) FROM submissions WHERE user_id = u.id AND status = 'APPROVED' AND type = 'INSTAGRAM_REELS'), 0)")
        elif task_type == "test":
            top_students_sql_parts.append("COALESCE((SELECT SUM(score) FROM test_scores WHERE user_id = u.id), 0)")
        elif task_type == "workshop":
            top_students_sql_parts.append("COALESCE((SELECT SUM(score) FROM workshop_scores WHERE user_id = u.id), 0)")
        elif task_type == "stories":
            top_students_sql_parts.append("COALESCE((SELECT SUM(score) FROM story_reports WHERE user_id = u.id AND status = 'APPROVED'), 0)")
        else:
            # all - umumiy ball
            top_students_sql_parts.append("COALESCE((SELECT SUM(score) FROM submissions WHERE user_id = u.id AND status = 'APPROVED'), 0)")
            top_students_sql_parts.append("COALESCE((SELECT SUM(score) FROM test_scores WHERE user_id = u.id), 0)")
            top_students_sql_parts.append("COALESCE((SELECT SUM(score) FROM workshop_scores WHERE user_id = u.id), 0)")
            top_students_sql_parts.append("COALESCE((SELECT SUM(score) FROM story_reports WHERE user_id = u.id AND status = 'APPROVED'), 0)")
            top_students_sql_parts.append("COALESCE((SELECT SUM(amount) FROM bonus_points WHERE user_id = u.id), 0)")
        
        score_sum = " + ".join(top_students_sql_parts)
        
        top_filter = "u.role = 'STUDENT' AND u.status = 'APPROVED'"
        if group_id:
            top_filter += f" AND u.group_id = {group_id}"
        
        top_sql = f"""
            SELECT u.id, u.full_name, u.username, g.name AS group_name,
                ({score_sum}) AS score
            FROM users u
            LEFT JOIN groups g ON g.id = u.group_id
            WHERE {top_filter}
            ORDER BY score DESC
            LIMIT 10
        """
        top_rows = (await session.execute(text(top_sql))).fetchall()
        top_students = [{
            "id": r.id,
            "full_name": r.full_name,
            "username": r.username,
            "group_name": r.group_name,
            "score": int(r.score or 0),
        } for r in top_rows]
        
        # Problem students - so'nggi ochiq darslarda topshirmaganlar
        problem_count = 0
        if task_type in ("konspekt", "workbook", "amaliy") and unlocked_with_data:
            sub_type = task_type.upper()
            last_3_lessons = [l["lesson_number"] for l in unlocked_with_data[-3:]]
            if last_3_lessons:
                lesson_ids_str = ",".join(str(n) for n in last_3_lessons)
                problem_filter = "u.role = 'STUDENT' AND u.status = 'APPROVED'"
                if group_id:
                    problem_filter += f" AND u.group_id = {group_id}"
                problem_sql = f"""
                    SELECT COUNT(*) AS c FROM users u
                    WHERE {problem_filter}
                    AND NOT EXISTS (
                        SELECT 1 FROM submissions s 
                        JOIN lessons l ON l.id = s.lesson_id
                        WHERE s.user_id = u.id AND s.status = 'APPROVED' 
                        AND s.type = '{sub_type}'
                        AND l.lesson_number IN ({lesson_ids_str})
                    )
                """
                problem_row = (await session.execute(text(problem_sql))).fetchone()
                problem_count = int(problem_row.c or 0)
        
        # Eng faol guruh (faqat umumiy uchun)
        most_active_group = None
        completion_pct = 0
        if group_id is None:
            active_row = (await session.execute(text("""
                SELECT g.name, COUNT(u.id) AS cnt 
                FROM groups g 
                LEFT JOIN users u ON u.group_id = g.id AND u.role = 'STUDENT' AND u.status = 'APPROVED'
                GROUP BY g.id, g.name 
                ORDER BY cnt DESC LIMIT 1
            """))).fetchone()
            if active_row:
                most_active_group = active_row.name
            
            comp_row = (await session.execute(text(
                "SELECT ROUND(COUNT(*) FILTER (WHERE is_unlocked = true) * 100.0 / NULLIF(COUNT(*), 0), 0) AS pct FROM lessons"
            ))).fetchone()
            completion_pct = int(comp_row.pct or 0) if comp_row else 0
        
        return {
            "task_type": task_type,
            "group_id": group_id,
            "lessons": lessons,
            "insights": {
                "best_lesson": best_lesson,
                "worst_lesson": worst_lesson,
                "biggest_rise": biggest_rise,
                "biggest_drop": biggest_drop,
            },
            "top_students": top_students,
            "problem_count": problem_count,
            "most_active_group": most_active_group,
            "completion_pct": completion_pct,
            "total_students": int(total_students.c or 0),
            "max_score": max_score,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail="Xato: " + str(e))


@app.get("/api/admin/trend/drilldown")
async def get_admin_trend_drilldown(
    lesson_number: int,
    task_type: str = "all",
    drilldown_type: str = "worst",  # best | worst | rise | drop
    group_id: int = None,
    session: AsyncSession = Depends(get_db),
):
    """Drilldown - har insight uchun batafsil ma'lumot"""
    try:
        # Dars ma'lumoti
        lesson_row = (await session.execute(text(
            "SELECT id, lesson_number, title, is_unlocked FROM lessons WHERE lesson_number = :ln"
        ), {"ln": lesson_number})).fetchone()
        
        if not lesson_row:
            raise HTTPException(status_code=404, detail="Dars topilmadi")
        
        lesson_id = lesson_row.id
        lesson_title = lesson_row.title
        
        # Vazifa turi -> source SQL va max ball
        type_map = {
            "konspekt": ("submissions", "KONSPEKT", 10),
            "workbook": ("submissions", "WORKBOOK", 20),
            "amaliy": ("submissions", "AMALIY", 50),
            "test": ("test_scores", None, 20),
            "workshop": ("workshop_scores", None, 50),
            "stories": ("story_reports", None, 25),
            "reels": ("submissions", "INSTAGRAM_REELS", 25),
        }
        
        if task_type == "all":
            # Hammasi - umumiy submissions
            source_table = "submissions"
            sub_type = None
            max_score_val = 100
        elif task_type in type_map:
            source_table, sub_type, max_score_val = type_map[task_type]
        else:
            raise HTTPException(status_code=400, detail="Noto'g'ri task_type")
        
        # SQL qurish - har bir o'quvchining shu dars uchun balli
        group_filter = ""
        params = {"lid": lesson_id}
        if group_id:
            group_filter = " AND u.group_id = :gid"
            params["gid"] = group_id
        
        if source_table == "submissions":
            sub_filter = f" AND s.type = '{sub_type}'" if sub_type else ""
            score_sql = f"""
                SELECT u.id, u.full_name, u.username, g.name AS group_name,
                    (SELECT s.score FROM submissions s WHERE s.user_id = u.id AND s.lesson_id = :lid AND s.status = 'APPROVED'{sub_filter} ORDER BY s.score DESC LIMIT 1) AS score
                FROM users u
                LEFT JOIN groups g ON g.id = u.group_id
                WHERE u.role = 'STUDENT' AND u.status = 'APPROVED'{group_filter}
                ORDER BY score DESC NULLS LAST
            """
        else:
            score_sql = f"""
                SELECT u.id, u.full_name, u.username, g.name AS group_name,
                    (SELECT score FROM {source_table} WHERE user_id = u.id AND lesson_id = :lid LIMIT 1) AS score
                FROM users u
                LEFT JOIN groups g ON g.id = u.group_id
                WHERE u.role = 'STUDENT' AND u.status = 'APPROVED'{group_filter}
                ORDER BY score DESC NULLS LAST
            """
        
        rows = (await session.execute(text(score_sql), params)).fetchall()
        
        # O'quvchilarni kategoriyalash
        students = []
        high_count = 0
        medium_count = 0
        low_count = 0
        missing_count = 0
        
        for row in rows:
            score = row.score
            if score is None:
                category = "missing"
                missing_count += 1
                score_val = None
            else:
                score_val = float(score)
                # Foiz hisoblash
                pct = (score_val / max_score_val) * 100 if max_score_val > 0 else 0
                if pct >= 80:
                    category = "high"
                    high_count += 1
                elif pct >= 50:
                    category = "medium"
                    medium_count += 1
                else:
                    category = "low"
                    low_count += 1
            
            students.append({
                "id": row.id,
                "full_name": row.full_name,
                "username": row.username,
                "group_name": row.group_name,
                "score": score_val,
                "category": category,
            })
        
        # Drilldown turiga qarab sort
        if drilldown_type == "worst":
            # Past ball va topshirmaganlar tepada
            students.sort(key=lambda s: (
                0 if s["category"] == "missing" else 1,
                s["score"] if s["score"] is not None else 0
            ))
        elif drilldown_type == "best":
            # Yuqori ball tepada
            students.sort(key=lambda s: -(s["score"] or 0))
        
        # Rise/Drop uchun previous lesson kerak
        previous_scores = {}
        previous_lesson_number = None
        if drilldown_type in ("rise", "drop"):
            # Avvalgi unlocked dars
            prev_row = (await session.execute(text(
                "SELECT id, lesson_number FROM lessons WHERE lesson_number < :ln AND is_unlocked = true ORDER BY lesson_number DESC LIMIT 1"
            ), {"ln": lesson_number})).fetchone()
            
            if prev_row:
                previous_lesson_number = prev_row.lesson_number
                prev_lid = prev_row.id
                # Avvalgi dars ballarini olish (har o'quvchi uchun)
                if source_table == "submissions":
                    sub_filter = f" AND s.type = '{sub_type}'" if sub_type else ""
                    prev_sql = f"""
                        SELECT user_id, MAX(score) AS score FROM submissions s
                        WHERE lesson_id = :lid AND status = 'APPROVED'{sub_filter}
                        GROUP BY user_id
                    """
                else:
                    prev_sql = f"""
                        SELECT user_id, MAX(score) AS score FROM {source_table}
                        WHERE lesson_id = :lid
                        GROUP BY user_id
                    """
                prev_rows = (await session.execute(text(prev_sql), {"lid": prev_lid})).fetchall()
                previous_scores = {r.user_id: float(r.score or 0) for r in prev_rows}
            
            # Har o'quvchiga change_percent qo'shish
            for st in students:
                prev = previous_scores.get(st["id"])
                curr = st["score"]
                if prev is not None and prev > 0 and curr is not None:
                    st["previous_score"] = prev
                    st["change_percent"] = round(((curr - prev) / prev) * 100, 1)
                else:
                    st["previous_score"] = None
                    st["change_percent"] = None
            
            # Filter va sort
            if drilldown_type == "rise":
                students = [s for s in students if s.get("change_percent") is not None and s["change_percent"] > 0]
                students.sort(key=lambda s: -s["change_percent"])
            else:  # drop
                students = [s for s in students if s.get("change_percent") is not None and s["change_percent"] < 0]
                students.sort(key=lambda s: s["change_percent"])
        
        # Avg va total hisoblash
        scored = [s for s in students if s["score"] is not None] if drilldown_type in ("best", "worst") else                  [s for s in students if s.get("score") is not None]
        avg_score = round(sum(s["score"] for s in scored) / len(scored), 1) if scored else 0
        total_students = len(rows)
        submitted_count = total_students - missing_count
        
        return {
            "lesson": {
                "number": lesson_number,
                "title": lesson_title,
                "max_score": max_score_val,
            },
            "task_type": task_type,
            "drilldown_type": drilldown_type,
            "stats": {
                "high": high_count,
                "medium": medium_count,
                "low": low_count,
                "missing": missing_count,
                "total": total_students,
                "submitted": submitted_count,
                "avg_score": avg_score,
                "completion_pct": round((submitted_count / total_students) * 100, 0) if total_students > 0 else 0,
            },
            "students": students,
            "previous_lesson_number": previous_lesson_number,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Xato: " + str(e))


@app.get("/api/admin/student/{user_id}/trend")
async def get_admin_student_trend(
    user_id: int,
    task_type: str = "all",
    session: AsyncSession = Depends(get_db),
):
    """Shaxsiy o'quvchi trend - 16 ta dars uchun"""
    try:
        # O'quvchi tekshirish
        user_row = (await session.execute(text(
            "SELECT id, full_name, username, group_id FROM users WHERE id = :uid AND role = 'STUDENT'"
        ), {"uid": user_id})).fetchone()
        
        if not user_row:
            raise HTTPException(status_code=404, detail="O'quvchi topilmadi")
        
        # Group nomi
        group_name = None
        if user_row.group_id:
            g_row = (await session.execute(text("SELECT name FROM groups WHERE id = :gid"), {"gid": user_row.group_id})).fetchone()
            if g_row:
                group_name = g_row.name
        
        # Vazifa turi -> max ball va source
        type_map = {
            "konspekt": ("submissions", "KONSPEKT", 10),
            "workbook": ("submissions", "WORKBOOK", 20),
            "amaliy": ("submissions", "AMALIY", 50),
            "test": ("test_scores", None, 20),
            "workshop": ("workshop_scores", None, 50),
            "stories": ("story_reports", None, 25),
            "reels": ("submissions", "INSTAGRAM_REELS", 25),
        }
        
        if task_type == "all":
            max_score_val = 100
            # Hammasi - submissions barcha turlari (KONSPEKT+WORKBOOK+AMALIY+REELS)
            score_sql = """
                SELECT l.lesson_number, l.is_unlocked, l.title,
                    COALESCE((SELECT SUM(score) FROM submissions WHERE user_id = :uid AND lesson_id = l.id AND status = 'APPROVED'), 0) +
                    COALESCE((SELECT score FROM test_scores WHERE user_id = :uid AND lesson_id = l.id LIMIT 1), 0) +
                    COALESCE((SELECT score FROM workshop_scores WHERE user_id = :uid AND lesson_id = l.id LIMIT 1), 0) +
                    COALESCE((SELECT score FROM story_reports WHERE user_id = :uid AND lesson_id = l.id AND status = 'APPROVED' LIMIT 1), 0) AS score
                FROM lessons l
                ORDER BY l.lesson_number
            """
        elif task_type in type_map:
            source_table, sub_type, max_score_val = type_map[task_type]
            if source_table == "submissions":
                score_sql = f"""
                    SELECT l.lesson_number, l.is_unlocked, l.title,
                        COALESCE((SELECT MAX(score) FROM submissions WHERE user_id = :uid AND lesson_id = l.id AND status = 'APPROVED' AND type = '{sub_type}'), 0) AS score
                    FROM lessons l
                    ORDER BY l.lesson_number
                """
            else:
                score_sql = f"""
                    SELECT l.lesson_number, l.is_unlocked, l.title,
                        COALESCE((SELECT score FROM {source_table} WHERE user_id = :uid AND lesson_id = l.id LIMIT 1), 0) AS score
                    FROM lessons l
                    ORDER BY l.lesson_number
                """
        else:
            raise HTTPException(status_code=400, detail="Noto'g'ri task_type")
        
        rows = (await session.execute(text(score_sql), {"uid": user_id})).fetchall()
        
        lessons = []
        for row in rows:
            score_val = float(row.score or 0)
            # Kategoriya
            if not row.is_unlocked:
                category = "locked"
            elif score_val == 0:
                category = "missing"
            else:
                pct = (score_val / max_score_val) * 100 if max_score_val > 0 else 0
                if pct >= 80:
                    category = "high"
                elif pct >= 50:
                    category = "medium"
                else:
                    category = "low"
            
            lessons.append({
                "lesson_number": row.lesson_number,
                "title": row.title,
                "is_unlocked": row.is_unlocked,
                "score": score_val,
                "category": category,
            })
        
        # Insights - faqat unlocked darslar
        unlocked = [l for l in lessons if l["is_unlocked"] and l["score"] > 0]
        
        best_lesson = max(unlocked, key=lambda x: x["score"]) if unlocked else None
        worst_lesson = min(unlocked, key=lambda x: x["score"]) if unlocked else None
        
        biggest_rise = None
        biggest_drop = None
        max_rise = 0
        max_drop = 0
        for i in range(1, len(unlocked)):
            prev = unlocked[i-1]
            curr = unlocked[i]
            if prev["score"] > 0:
                change_pct = ((curr["score"] - prev["score"]) / prev["score"]) * 100
                if change_pct > max_rise:
                    max_rise = change_pct
                    biggest_rise = {"from": prev["lesson_number"], "to": curr["lesson_number"], "percent": round(change_pct, 1), "from_score": prev["score"], "to_score": curr["score"]}
                if change_pct < max_drop:
                    max_drop = change_pct
                    biggest_drop = {"from": prev["lesson_number"], "to": curr["lesson_number"], "percent": round(change_pct, 1), "from_score": prev["score"], "to_score": curr["score"]}
        
        return {
            "user": {
                "id": user_row.id,
                "full_name": user_row.full_name,
                "username": user_row.username,
                "group_name": group_name,
            },
            "task_type": task_type,
            "max_score": max_score_val,
            "lessons": lessons,
            "insights": {
                "best_lesson": best_lesson,
                "worst_lesson": worst_lesson,
                "biggest_rise": biggest_rise,
                "biggest_drop": biggest_drop,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Xato: " + str(e))


@app.get("/api/admin/submissions/pending")
async def get_admin_pending_submissions(
    task_type: str = "all",
    group_id: int = None,
    sort: str = "oldest",
    session: AsyncSession = Depends(get_db),
):
    """Baholanmagan vazifalar"""
    try:
        # Stats
        stats_sql = """
            SELECT 
                COUNT(*) AS total,
                SUM(CASE WHEN s.type = 'KONSPEKT' THEN 1 ELSE 0 END) AS konspekt,
                SUM(CASE WHEN s.type = 'WORKBOOK' THEN 1 ELSE 0 END) AS workbook,
                SUM(CASE WHEN s.type = 'AMALIY' THEN 1 ELSE 0 END) AS amaliy,
                SUM(CASE WHEN s.type = 'INSTAGRAM_STORIES' THEN 1 ELSE 0 END) AS stories,
                SUM(CASE WHEN s.type = 'INSTAGRAM_REELS' THEN 1 ELSE 0 END) AS reels,
                MIN(s.submitted_at) AS oldest_at
            FROM submissions s
            JOIN users u ON u.id = s.user_id
            WHERE s.status = 'PENDING'
        """
        stats_params = {}
        if group_id:
            stats_sql += " AND u.group_id = :gid"
            stats_params["gid"] = group_id
        
        stats_row = (await session.execute(text(stats_sql), stats_params)).fetchone()
        
        # Submissions
        sub_sql = """
            SELECT 
                s.id, s.type, s.file_id, s.file_name, s.file_type, 
                s.instagram_link, s.is_late, s.submitted_at, s.max_score,
                u.id AS user_id, u.full_name AS user_full_name, u.username AS user_username,
                u.group_id, g.name AS group_name, c.full_name AS curator_name,
                l.lesson_number, l.title AS lesson_title, l.speaker
            FROM submissions s
            JOIN users u ON u.id = s.user_id
            LEFT JOIN groups g ON g.id = u.group_id
            LEFT JOIN users c ON c.id = g.curator_id
            LEFT JOIN lessons l ON l.id = s.lesson_id
            WHERE s.status = 'PENDING'
        """
        sub_params = {}
        if group_id:
            sub_sql += " AND u.group_id = :gid"
            sub_params["gid"] = group_id
        
        if task_type != "all":
            type_map_sub = {
                "konspekt": "KONSPEKT", "workbook": "WORKBOOK", "amaliy": "AMALIY",
                "stories": "INSTAGRAM_STORIES", "reels": "INSTAGRAM_REELS"
            }
            if task_type in type_map_sub:
                sub_sql += " AND s.type = :tt"
                sub_params["tt"] = type_map_sub[task_type]
        
        # Sorting
        if sort == "newest":
            sub_sql += " ORDER BY s.submitted_at DESC"
        elif sort == "lesson":
            sub_sql += " ORDER BY l.lesson_number ASC, s.submitted_at ASC"
        elif sort == "student":
            sub_sql += " ORDER BY u.full_name ASC"
        else:  # oldest
            sub_sql += " ORDER BY s.submitted_at ASC"
        
        sub_rows = (await session.execute(text(sub_sql), sub_params)).fetchall()
        
        submissions = []
        for r in sub_rows:
            submissions.append({
                "id": r.id,
                "type": r.type,
                "user_id": r.user_id,
                "user_full_name": r.user_full_name,
                "user_username": r.user_username,
                "group_id": r.group_id,
                "group_name": r.group_name,
                "curator_name": r.curator_name,
                "lesson_number": r.lesson_number,
                "lesson_title": r.lesson_title,
                "speaker": r.speaker,
                "file_id": r.file_id,
                "file_name": r.file_name,
                "file_type": r.file_type,
                "instagram_link": r.instagram_link,
                "is_late": r.is_late,
                "max_score": r.max_score,
                "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
            })
        
        return {
            "stats": {
                "total": int(stats_row.total or 0),
                "by_type": {
                    "konspekt": int(stats_row.konspekt or 0),
                    "workbook": int(stats_row.workbook or 0),
                    "amaliy": int(stats_row.amaliy or 0),
                    "stories": int(stats_row.stories or 0),
                    "reels": int(stats_row.reels or 0),
                },
                "oldest_at": stats_row.oldest_at.isoformat() if stats_row.oldest_at else None,
            },
            "submissions": submissions,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail="Xato: " + str(e))


@app.post("/api/admin/submission/{submission_id}/review")
async def review_submission(
    submission_id: int,
    payload: dict,
    session: AsyncSession = Depends(get_db),
):
    """Vazifani baholash (tasdiqlash/rad etish)"""
    try:
        score = payload.get("score", 0)
        comment = payload.get("comment", "")
        approved = payload.get("approved", True)
        is_late = payload.get("is_late", False)
        reviewer_telegram_id = payload.get("reviewer_telegram_id")
        
        # Reviewer ID ni topish
        reviewer_id = None
        if reviewer_telegram_id:
            r = (await session.execute(text(
                "SELECT id FROM users WHERE telegram_id = :tg"
            ), {"tg": reviewer_telegram_id})).fetchone()
            if r:
                reviewer_id = r.id
        
        # Status va ball
        status_val = "APPROVED" if approved else "REJECTED"
        final_score = int(score) if approved else 0
        
        # DB update
        update_sql = """
            UPDATE submissions 
            SET status = :status, 
                score = :score, 
                feedback = :feedback, 
                reviewer_id = :reviewer_id,
                is_late = :is_late,
                reviewed_at = NOW()
            WHERE id = :sid AND status = 'PENDING'
            RETURNING id, user_id, type, lesson_id
        """
        result = (await session.execute(text(update_sql), {
            "status": status_val,
            "score": final_score,
            "feedback": comment[:500] if comment else None,
            "reviewer_id": reviewer_id,
            "is_late": is_late,
            "sid": submission_id,
        })).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Vazifa topilmadi yoki allaqachon baholangan")
        
        await session.commit()
        
        return {
            "success": True,
            "submission_id": result.id,
            "status": status_val,
            "score": final_score,
            "message": "Tasdiqlandi" if approved else "Rad etildi",
        }
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail="Xato: " + str(e))


@app.get("/api/admin/groups")
async def get_admin_groups(session: AsyncSession = Depends(get_db)):
    """4 ta guruh ma'lumotlari (bot formulasi bilan)"""
    try:
        rows = (await session.execute(text(
            "SELECT g.id, g.name, "
            "c.id AS curator_id, c.full_name AS curator_name, c.telegram_id AS curator_tg, c.username AS curator_username, "
            "(SELECT COUNT(*) FROM users WHERE group_id = g.id AND status = 'APPROVED' AND role = 'STUDENT') AS students_count, "
            "COALESCE(("
            "  SELECT ROUND(AVG(total_score)) FROM ("
            "    SELECT "
            "      COALESCE((SELECT SUM(score) FROM submissions WHERE user_id = u.id AND status = 'APPROVED'), 0) + "
            "      COALESCE((SELECT SUM(score) FROM test_scores WHERE user_id = u.id), 0) + "
            "      COALESCE((SELECT SUM(score) FROM workshop_scores WHERE user_id = u.id), 0) + "
            "      COALESCE((SELECT SUM(score) FROM story_reports WHERE user_id = u.id AND status = 'APPROVED'), 0) + "
            "      COALESCE((SELECT SUM(amount) FROM bonus_points WHERE user_id = u.id), 0) AS total_score "
            "    FROM users u WHERE u.group_id = g.id AND u.status = 'APPROVED' AND u.role = 'STUDENT'"
            "  ) sub"
            "), 0) AS avg_score "
            "FROM groups g "
            "LEFT JOIN users c ON c.id = g.curator_id "
            "ORDER BY g.id"
        ))).fetchall()
        
        groups = []
        for row in rows:
            groups.append({
                "id": row.id,
                "name": row.name,
                "curator_id": row.curator_id,
                "curator_name": row.curator_name,
                "curator_telegram_id": row.curator_tg,
                "curator_username": row.curator_username,
                "students_count": int(row.students_count or 0),
                "avg_score": int(row.avg_score or 0),
            })
        
        # Saralash: avg_score bo'yicha (rank uchun)
        sorted_groups = sorted(groups, key=lambda g: g["avg_score"], reverse=True)
        for i, g in enumerate(sorted_groups):
            g["rank"] = i + 1
        
        return {"groups": sorted_groups}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Xato: " + str(e))


@app.get("/api/admin/student/{user_id}")
async def get_admin_student_detail(user_id: int, session: AsyncSession = Depends(get_db)):
    """Bot bilan bir xil ball formulasi"""
    try:
        row = (await session.execute(text(
            "SELECT u.id, u.telegram_id, u.full_name, u.username, u.phone, u.registered_at, "
            "g.id AS group_id, g.name AS group_name, c.full_name AS curator_name, "
            "COALESCE((SELECT SUM(score) FROM submissions WHERE user_id = u.id AND status = 'APPROVED' AND type = 'KONSPEKT'), 0) + "
            "  COALESCE((SELECT SUM(amount) FROM bonus_points WHERE user_id = u.id AND submission_type = 'konspekt'), 0) AS konspekt, "
            "COALESCE((SELECT SUM(score) FROM submissions WHERE user_id = u.id AND status = 'APPROVED' AND type = 'WORKBOOK'), 0) + "
            "  COALESCE((SELECT SUM(amount) FROM bonus_points WHERE user_id = u.id AND submission_type = 'workbook'), 0) AS workbook, "
            "COALESCE((SELECT SUM(score) FROM submissions WHERE user_id = u.id AND status = 'APPROVED' AND type = 'AMALIY'), 0) + "
            "  COALESCE((SELECT SUM(amount) FROM bonus_points WHERE user_id = u.id AND submission_type = 'amaliy'), 0) AS amaliy, "
            "COALESCE((SELECT SUM(score) FROM test_scores WHERE user_id = u.id), 0) + "
            "  COALESCE((SELECT SUM(amount) FROM bonus_points WHERE user_id = u.id AND submission_type = 'test'), 0) AS test, "
            "COALESCE((SELECT SUM(score) FROM workshop_scores WHERE user_id = u.id), 0) + "
            "  COALESCE((SELECT SUM(amount) FROM bonus_points WHERE user_id = u.id AND submission_type = 'workshop'), 0) AS workshop, "
            "COALESCE((SELECT SUM(score) FROM story_reports WHERE user_id = u.id AND status = 'APPROVED'), 0) + "
            "  COALESCE((SELECT SUM(score) FROM submissions WHERE user_id = u.id AND status = 'APPROVED' AND type = 'INSTAGRAM_STORIES'), 0) + "
            "  COALESCE((SELECT SUM(amount) FROM bonus_points WHERE user_id = u.id AND submission_type = 'stories'), 0) AS stories, "
            "COALESCE((SELECT SUM(score) FROM submissions WHERE user_id = u.id AND status = 'APPROVED' AND type = 'INSTAGRAM_REELS'), 0) + "
            "  COALESCE((SELECT SUM(amount) FROM bonus_points WHERE user_id = u.id AND submission_type = 'reels'), 0) AS reels, "
            "COALESCE((SELECT SUM(amount) FROM bonus_points WHERE user_id = u.id AND submission_type = 'extra'), 0) AS bonus, "
            "(SELECT COUNT(*) FROM lesson_attendance WHERE user_id = u.id AND status = 'ON_TIME') AS att_present, "
            "(SELECT COUNT(*) FROM lesson_attendance WHERE user_id = u.id AND status IN ('LATE_TIER_1','LATE_TIER_2','LATE_TIER_3')) AS att_late, "
            "(SELECT COUNT(*) FROM lesson_attendance WHERE user_id = u.id AND status = 'ABSENT') AS att_absent, "
            "COALESCE((SELECT SUM(amount_uzs) FROM fines WHERE user_id = u.id AND status = 'UNPAID'), 0) AS fine_unpaid, "
            "COALESCE((SELECT SUM(amount_uzs) FROM fines WHERE user_id = u.id AND status = 'PAID'), 0) AS fine_paid "
            "FROM users u LEFT JOIN groups g ON g.id = u.group_id LEFT JOIN users c ON c.id = g.curator_id "
            "WHERE u.id = :uid"
        ), {"uid": user_id})).fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="O'quvchi topilmadi")
        
        scores = {
            "konspekt": int(row.konspekt), "workbook": int(row.workbook), "amaliy": int(row.amaliy),
            "test": int(row.test), "workshop": int(row.workshop), "stories": int(row.stories),
            "reels": int(row.reels), "bonus": int(row.bonus),
        }
        total = sum(scores.values())
        
        rank_row = (await session.execute(text(
            "WITH all_scores AS ("
            "SELECT u.id, "
            "COALESCE((SELECT SUM(score) FROM submissions WHERE user_id = u.id AND status = 'APPROVED'), 0) + "
            "COALESCE((SELECT SUM(score) FROM test_scores WHERE user_id = u.id), 0) + "
            "COALESCE((SELECT SUM(score) FROM workshop_scores WHERE user_id = u.id), 0) + "
            "COALESCE((SELECT SUM(score) FROM story_reports WHERE user_id = u.id AND status = 'APPROVED'), 0) + "
            "COALESCE((SELECT SUM(amount) FROM bonus_points WHERE user_id = u.id), 0) AS total "
            "FROM users u WHERE u.role = 'STUDENT' AND u.status = 'APPROVED') "
            "SELECT COUNT(*) + 1 AS rank FROM all_scores WHERE total > (SELECT total FROM all_scores WHERE id = :uid)"
        ), {"uid": user_id})).fetchone()
        # Note: rank bot bilan bir xil - bonus_points har turi
        
        return {
            "id": row.id,
            "telegram_id": row.telegram_id,
            "full_name": row.full_name,
            "username": row.username,
            "phone": row.phone,
            "registered_at": row.registered_at.isoformat() if row.registered_at else None,
            "group_id": row.group_id,
            "group_name": row.group_name,
            "curator_name": row.curator_name,
            "scores": scores,
            "total_score": total,
            "rank": int(rank_row.rank or 1),
            "attendance": {
                "present": int(row.att_present or 0),
                "late": int(row.att_late or 0),
                "absent": int(row.att_absent or 0),
            },
            "fines": {
                "unpaid_uzs": int(row.fine_unpaid or 0),
                "paid_uzs": int(row.fine_paid or 0),
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Xato: " + str(e))


@app.get("/api/admin/students")
async def get_admin_students(session: AsyncSession = Depends(get_db)):
    """Hamma tasdiqlangan o'quvchilar ro'yxati ballar bilan"""
    try:
        # 46 ta student + bot formulasi
        students_res = await session.execute(text("""
            SELECT 
                u.id, u.telegram_id, u.full_name, u.username,
                g.id AS group_id, g.name AS group_name,
                c.full_name AS curator_name,
                COALESCE((SELECT SUM(score) FROM submissions WHERE user_id = u.id AND status = 'APPROVED'), 0) AS sub_score,
                COALESCE((SELECT SUM(score) FROM test_scores WHERE user_id = u.id), 0) AS test_score,
                COALESCE((SELECT SUM(score) FROM workshop_scores WHERE user_id = u.id), 0) AS workshop_score,
                COALESCE((SELECT SUM(score) FROM story_reports WHERE user_id = u.id AND status = 'APPROVED'), 0) AS story_score,
                COALESCE((SELECT SUM(amount) FROM bonus_points WHERE user_id = u.id), 0) AS bonus_score
            FROM users u
            LEFT JOIN groups g ON g.id = u.group_id
            LEFT JOIN users c ON c.id = g.curator_id
            WHERE u.role = 'STUDENT' AND u.status = 'APPROVED'
            ORDER BY u.full_name
        """))
        
        students = []
        for row in students_res.fetchall():
            # Bot formulasi: sub_score'da KONSPEKT+WORKBOOK+AMALIY+REELS+STORIES_OLD bor
            # bonus_score'da hammasi yig'ilgan (har turi uchun)
            total = int(row.sub_score) + int(row.test_score) + int(row.workshop_score) + int(row.story_score) + int(row.bonus_score)
            students.append({
                "id": row.id,
                "telegram_id": row.telegram_id,
                "full_name": row.full_name,
                "username": row.username,
                "group_id": row.group_id,
                "group_name": row.group_name,
                "curator_name": row.curator_name,
                "total_score": total,
            })
        
        # Reyting hisoblash
        sorted_by_score = sorted(students, key=lambda s: s["total_score"], reverse=True)
        for i, s in enumerate(sorted_by_score):
            s["rank"] = i + 1
        
        return {"students": sorted_by_score, "max_total": 1970}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Xato: {str(e)}")


@app.get("/api/admin/users/stats")
async def get_admin_users_stats(session: AsyncSession = Depends(get_db)):
    """Foydalanuvchilar bo'limi ustun statistikalari"""
    try:
        row = (await session.execute(text("""
            SELECT 
                (SELECT COUNT(*) FROM users WHERE status = 'PENDING') AS pending,
                (SELECT COUNT(*) FROM users WHERE status = 'APPROVED' AND role = 'STUDENT') AS approved_students,
                (SELECT COUNT(*) FROM groups) AS groups,
                (SELECT COUNT(*) FROM users WHERE role = 'CURATOR' AND status = 'APPROVED') AS curators,
                (SELECT COUNT(*) FROM users WHERE role = 'ASSISTANT' AND status = 'APPROVED') AS assistants,
                (SELECT COUNT(*) FROM users WHERE status = 'REJECTED') AS blocked
        """))).fetchone()
        return {
            "pending": int(row.pending or 0),
            "approved_students": int(row.approved_students or 0),
            "groups": int(row.groups or 0),
            "curators": int(row.curators or 0),
            "assistants": int(row.assistants or 0),
            "blocked": int(row.blocked or 0),
        }
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
