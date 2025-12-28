-- Google Timeline Analyzer Database Schema
-- PostgreSQL 16 with PostGIS 3.4

-- Enable PostGIS extension
CREATE EXTENSION IF NOT EXISTS postgis;

-- Core timeline segments table
CREATE TABLE IF NOT EXISTS timeline_segments (
    id SERIAL PRIMARY KEY,
    segment_type VARCHAR(20) NOT NULL,
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    duration_seconds INTEGER GENERATED ALWAYS AS (
        EXTRACT(EPOCH FROM (end_time - start_time))::INTEGER
    ) STORED,
    timezone_offset_minutes INTEGER,
    raw_data JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_segments_time ON timeline_segments(start_time, end_time);
CREATE INDEX IF NOT EXISTS idx_segments_type ON timeline_segments(segment_type);
CREATE INDEX IF NOT EXISTS idx_segments_start_time ON timeline_segments(start_time);

-- Visits table
CREATE TABLE IF NOT EXISTS visits (
    id SERIAL PRIMARY KEY,
    segment_id INTEGER REFERENCES timeline_segments(id) ON DELETE CASCADE UNIQUE,
    place_id VARCHAR(100),
    semantic_type VARCHAR(50),
    probability FLOAT,
    location GEOGRAPHY(POINT, 4326),
    hierarchy_level INTEGER
);

CREATE INDEX IF NOT EXISTS idx_visits_place ON visits(place_id);
CREATE INDEX IF NOT EXISTS idx_visits_semantic ON visits(semantic_type);
CREATE INDEX IF NOT EXISTS idx_visits_location ON visits USING GIST(location);
CREATE INDEX IF NOT EXISTS idx_visits_segment ON visits(segment_id);

-- Activities table
CREATE TABLE IF NOT EXISTS activities (
    id SERIAL PRIMARY KEY,
    segment_id INTEGER REFERENCES timeline_segments(id) ON DELETE CASCADE UNIQUE,
    start_location GEOGRAPHY(POINT, 4326),
    end_location GEOGRAPHY(POINT, 4326),
    distance_meters FLOAT,
    activity_type VARCHAR(50),
    probability FLOAT
);

CREATE INDEX IF NOT EXISTS idx_activities_type ON activities(activity_type);
CREATE INDEX IF NOT EXISTS idx_activities_distance ON activities(distance_meters);
CREATE INDEX IF NOT EXISTS idx_activities_start_location ON activities USING GIST(start_location);
CREATE INDEX IF NOT EXISTS idx_activities_end_location ON activities USING GIST(end_location);
CREATE INDEX IF NOT EXISTS idx_activities_segment ON activities(segment_id);

-- Timeline paths (position snapshots)
CREATE TABLE IF NOT EXISTS timeline_paths (
    id SERIAL PRIMARY KEY,
    segment_id INTEGER REFERENCES timeline_segments(id) ON DELETE CASCADE,
    location GEOGRAPHY(POINT, 4326),
    recorded_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_paths_time ON timeline_paths(recorded_at);
CREATE INDEX IF NOT EXISTS idx_paths_location ON timeline_paths USING GIST(location);
CREATE INDEX IF NOT EXISTS idx_paths_segment ON timeline_paths(segment_id);

-- Timeline memories (Google-identified trips)
CREATE TABLE IF NOT EXISTS timeline_memories (
    id SERIAL PRIMARY KEY,
    segment_id INTEGER REFERENCES timeline_segments(id) ON DELETE CASCADE UNIQUE,
    distance_from_origin_kms INTEGER,
    destination_place_ids TEXT[]
);

CREATE INDEX IF NOT EXISTS idx_memories_segment ON timeline_memories(segment_id);
CREATE INDEX IF NOT EXISTS idx_memories_destinations ON timeline_memories USING GIN(destination_place_ids);

-- Places table (cached Google Places API data)
CREATE TABLE IF NOT EXISTS places (
    place_id VARCHAR(100) PRIMARY KEY,
    name VARCHAR(500),
    formatted_address TEXT,
    types TEXT[],
    location GEOGRAPHY(POINT, 4326),
    rating FLOAT,
    user_ratings_total INTEGER,
    price_level INTEGER,
    photo_references TEXT[],
    business_status VARCHAR(50),
    api_response JSONB,
    last_updated TIMESTAMPTZ DEFAULT NOW(),
    fetch_attempts INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_places_location ON places USING GIST(location);
CREATE INDEX IF NOT EXISTS idx_places_types ON places USING GIN(types);
CREATE INDEX IF NOT EXISTS idx_places_name_fulltext ON places USING GIN(to_tsvector('english', COALESCE(name, '')));
CREATE INDEX IF NOT EXISTS idx_places_address_fulltext ON places USING GIN(to_tsvector('english', COALESCE(formatted_address, '')));

-- Detected trips table
CREATE TABLE IF NOT EXISTS trips (
    id SERIAL PRIMARY KEY,
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    origin_place_id VARCHAR(100) REFERENCES places(place_id),
    is_multi_day BOOLEAN,
    total_distance_meters FLOAT,
    primary_transport_mode VARCHAR(50),
    detection_algorithm VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trips_time ON trips(start_time, end_time);
CREATE INDEX IF NOT EXISTS idx_trips_algorithm ON trips(detection_algorithm);
CREATE INDEX IF NOT EXISTS idx_trips_origin ON trips(origin_place_id);

-- Trip destinations (many-to-many)
CREATE TABLE IF NOT EXISTS trip_destinations (
    trip_id INTEGER REFERENCES trips(id) ON DELETE CASCADE,
    place_id VARCHAR(100) REFERENCES places(place_id),
    visit_order INTEGER,
    PRIMARY KEY (trip_id, place_id)
);

CREATE INDEX IF NOT EXISTS idx_trip_destinations_trip ON trip_destinations(trip_id);
CREATE INDEX IF NOT EXISTS idx_trip_destinations_place ON trip_destinations(place_id);

-- Trip segments (which activities/visits belong to which trip)
CREATE TABLE IF NOT EXISTS trip_segments (
    trip_id INTEGER REFERENCES trips(id) ON DELETE CASCADE,
    segment_id INTEGER REFERENCES timeline_segments(id),
    segment_order INTEGER,
    PRIMARY KEY (trip_id, segment_id)
);

CREATE INDEX IF NOT EXISTS idx_trip_segments_trip ON trip_segments(trip_id);
CREATE INDEX IF NOT EXISTS idx_trip_segments_segment ON trip_segments(segment_id);

-- User location profile
CREATE TABLE IF NOT EXISTS user_profile (
    id SERIAL PRIMARY KEY,
    home_place_id VARCHAR(100) REFERENCES places(place_id),
    work_place_id VARCHAR(100) REFERENCES places(place_id),
    home_location GEOGRAPHY(POINT, 4326),
    work_location GEOGRAPHY(POINT, 4326)
);

CREATE INDEX IF NOT EXISTS idx_user_profile_home_location ON user_profile USING GIST(home_location);
CREATE INDEX IF NOT EXISTS idx_user_profile_work_location ON user_profile USING GIST(work_location);

-- Travel mode affinities
CREATE TABLE IF NOT EXISTS travel_mode_affinities (
    mode VARCHAR(50) PRIMARY KEY,
    affinity FLOAT
);

-- Useful views for common queries

-- View: Recent trips with destinations
CREATE OR REPLACE VIEW recent_trips_with_destinations AS
SELECT
    t.id,
    t.start_time,
    t.end_time,
    t.total_distance_meters / 1000 AS distance_km,
    EXTRACT(EPOCH FROM (t.end_time - t.start_time)) / 3600 AS duration_hours,
    t.primary_transport_mode,
    t.detection_algorithm,
    array_agg(p.name ORDER BY td.visit_order) AS destination_names,
    array_agg(p.place_id ORDER BY td.visit_order) AS destination_place_ids
FROM trips t
LEFT JOIN trip_destinations td ON t.id = td.trip_id
LEFT JOIN places p ON td.place_id = p.place_id
GROUP BY t.id, t.start_time, t.end_time, t.total_distance_meters,
         t.primary_transport_mode, t.detection_algorithm;

-- View: Most visited places
CREATE OR REPLACE VIEW most_visited_places AS
SELECT
    v.place_id,
    p.name,
    p.formatted_address,
    COUNT(v.id) AS visit_count,
    MIN(ts.start_time) AS first_visit,
    MAX(ts.start_time) AS last_visit,
    p.rating,
    p.types
FROM visits v
JOIN timeline_segments ts ON v.segment_id = ts.id
LEFT JOIN places p ON v.place_id = p.place_id
WHERE v.semantic_type NOT IN ('HOME', 'WORK', 'INFERRED_HOME', 'INFERRED_WORK')
GROUP BY v.place_id, p.name, p.formatted_address, p.rating, p.types
ORDER BY visit_count DESC;

-- View: Activity statistics by type
CREATE OR REPLACE VIEW activity_statistics AS
SELECT
    a.activity_type,
    COUNT(*) AS activity_count,
    SUM(a.distance_meters) / 1000 AS total_km,
    AVG(a.distance_meters) / 1000 AS avg_km_per_activity,
    SUM(ts.duration_seconds) / 3600 AS total_hours
FROM activities a
JOIN timeline_segments ts ON a.segment_id = ts.id
GROUP BY a.activity_type
ORDER BY total_km DESC;

-- View: Travel statistics by year
CREATE OR REPLACE VIEW yearly_travel_statistics AS
SELECT
    EXTRACT(YEAR FROM ts.start_time) AS year,
    COUNT(DISTINCT CASE WHEN ts.segment_type = 'visit' THEN ts.id END) AS visit_count,
    COUNT(DISTINCT CASE WHEN ts.segment_type = 'activity' THEN ts.id END) AS activity_count,
    SUM(CASE WHEN ts.segment_type = 'activity' THEN a.distance_meters ELSE 0 END) / 1000 AS total_km,
    COUNT(DISTINCT v.place_id) AS unique_places_visited
FROM timeline_segments ts
LEFT JOIN activities a ON ts.id = a.segment_id
LEFT JOIN visits v ON ts.id = v.segment_id
GROUP BY year
ORDER BY year;

-- Function: Calculate distance between two places
CREATE OR REPLACE FUNCTION calculate_distance_km(place1_id VARCHAR, place2_id VARCHAR)
RETURNS FLOAT AS $$
DECLARE
    distance FLOAT;
BEGIN
    SELECT ST_Distance(p1.location, p2.location) / 1000 INTO distance
    FROM places p1, places p2
    WHERE p1.place_id = place1_id AND p2.place_id = place2_id;

    RETURN distance;
END;
$$ LANGUAGE plpgsql;

-- Function: Find places within radius of a location
CREATE OR REPLACE FUNCTION find_places_within_radius(
    center_lat FLOAT,
    center_lng FLOAT,
    radius_km FLOAT
)
RETURNS TABLE (
    place_id VARCHAR,
    name VARCHAR,
    formatted_address TEXT,
    distance_km FLOAT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        p.place_id,
        p.name,
        p.formatted_address,
        ST_Distance(
            p.location,
            ST_SetSRID(ST_MakePoint(center_lng, center_lat), 4326)::geography
        ) / 1000 AS distance_km
    FROM places p
    WHERE ST_DWithin(
        p.location,
        ST_SetSRID(ST_MakePoint(center_lng, center_lat), 4326)::geography,
        radius_km * 1000
    )
    ORDER BY distance_km;
END;
$$ LANGUAGE plpgsql;

-- Grant permissions (if needed)
-- GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO timeline_user;
-- GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO timeline_user;

-- Add comments for documentation
COMMENT ON TABLE timeline_segments IS 'Core table storing all timeline events (visits, activities, paths, memories)';
COMMENT ON TABLE visits IS 'Visit segments representing time spent at specific locations';
COMMENT ON TABLE activities IS 'Activity segments representing movement between locations';
COMMENT ON TABLE places IS 'Cached Google Places API data for visited locations';
COMMENT ON TABLE trips IS 'Detected trips using various algorithms';
COMMENT ON COLUMN places.api_response IS 'Full JSON response from Google Places API for future reference';
COMMENT ON COLUMN trips.detection_algorithm IS 'Algorithm used: memory, home, overnight, or distance';
