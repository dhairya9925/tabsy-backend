CREATE OR REPLACE FUNCTION delete_user_account()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  -- We delete from auth.users. The user's ID is auth.uid().
  
  -- Delete all their group memberships
  DELETE FROM public.group_members WHERE user_id = auth.uid();
  
  -- Delete all their expense splits
  DELETE FROM public.expense_splits WHERE user_id = auth.uid();
  
  -- Delete all expenses they paid for or created
  DELETE FROM public.expenses WHERE user_id = auth.uid() OR paid_by = auth.uid();
  
  -- Delete friends where they are involved
  DELETE FROM public.friends WHERE user_id = auth.uid() OR friend_id = auth.uid();
  
  -- Delete their profile
  DELETE FROM public.profiles WHERE user_id = auth.uid();
  
  -- Finally, delete the user from auth schema
  DELETE FROM auth.users WHERE id = auth.uid();
END;
$$;
