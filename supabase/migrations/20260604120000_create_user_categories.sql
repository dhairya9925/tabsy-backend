-- Create user_categories table for custom per-user expense categories
CREATE TABLE user_categories (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  slug TEXT NOT NULL,
  color_index INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(user_id, slug)
);

-- RLS: users can only see/manage their own categories
ALTER TABLE user_categories ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own categories"
  ON user_categories FOR SELECT TO authenticated
  USING (user_id = auth.uid());

CREATE POLICY "Users can insert own categories"
  ON user_categories FOR INSERT TO authenticated
  WITH CHECK (user_id = auth.uid());

CREATE POLICY "Users can delete own categories"
  ON user_categories FOR DELETE TO authenticated
  USING (user_id = auth.uid());
