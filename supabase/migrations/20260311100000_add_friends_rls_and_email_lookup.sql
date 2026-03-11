-- Phase 2: Secure Email Lookup & RLS Policies for Friends/Shadow Profiles

-- =============================================
-- 1. Enable RLS on the friends table
-- =============================================
ALTER TABLE IF EXISTS public.friends ENABLE ROW LEVEL SECURITY;

-- =============================================
-- 2. RLS Policies for friends table
-- =============================================
DROP POLICY IF EXISTS "Users can view their own friend records" ON friends;
DROP POLICY IF EXISTS "Users can insert friend requests" ON friends;
DROP POLICY IF EXISTS "Users can update friend requests sent to them" ON friends;
DROP POLICY IF EXISTS "Users can delete their own friend records" ON friends;

CREATE POLICY "Users can view their own friend records" ON friends
  FOR SELECT TO authenticated
  USING (user_id = auth.uid() OR friend_id = auth.uid());

CREATE POLICY "Users can insert friend requests" ON friends
  FOR INSERT TO authenticated
  WITH CHECK (user_id = auth.uid());

CREATE POLICY "Users can update friend requests sent to them" ON friends
  FOR UPDATE TO authenticated
  USING (friend_id = auth.uid());

CREATE POLICY "Users can delete their own friend records" ON friends
  FOR DELETE TO authenticated
  USING (user_id = auth.uid() OR friend_id = auth.uid());

-- =============================================
-- 3. Additional RLS Policies for profiles (shadow profiles)
-- =============================================
DROP POLICY IF EXISTS "Users can view shadow profiles they created" ON profiles;
DROP POLICY IF EXISTS "Users can insert shadow profiles" ON profiles;

CREATE POLICY "Users can view shadow profiles they created" ON profiles
  FOR SELECT TO authenticated
  USING (is_shadow = true AND shadow_created_by = auth.uid());

CREATE POLICY "Users can insert shadow profiles" ON profiles
  FOR INSERT TO authenticated
  WITH CHECK (is_shadow = true AND shadow_created_by = auth.uid());

-- =============================================
-- 4. Secure Email Lookup Function (SECURITY DEFINER)
-- =============================================
-- This function performs an exact-match email lookup.
-- It runs with SECURITY DEFINER to bypass RLS, but only returns
-- limited data (user_id, display_name, avatar_url).
-- It ignores shadow profiles to prevent leaking private contacts.

CREATE OR REPLACE FUNCTION public.get_profile_by_exact_email(search_email text)
RETURNS TABLE (
  user_id uuid,
  display_name text,
  avatar_url text
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT p.user_id, p.display_name, p.avatar_url
  FROM public.profiles p
  WHERE p.email = search_email
    AND p.is_shadow = false
  LIMIT 1;
$$;
