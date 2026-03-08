
-- Allow users to view profiles of people in their groups (needed for member lists)
CREATE POLICY "Users can view profiles of group members"
  ON public.profiles FOR SELECT TO authenticated
  USING (
    user_id IN (
      SELECT gm.user_id FROM public.group_members gm
      WHERE gm.group_id IN (
        SELECT gm2.group_id FROM public.group_members gm2
        WHERE gm2.user_id = auth.uid()
      )
    )
  );
