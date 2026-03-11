-- Phase 3: Merge Shadow Profile Function
-- This function safely merges a shadow profile into a real authenticated user.
-- It runs inside a transaction to prevent partial updates.

CREATE OR REPLACE FUNCTION public.merge_shadow_profile(shadow_user_id uuid, real_user_id uuid)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  shadow_email text;
  real_email text;
BEGIN
  -- Step 1: Validate the shadow profile exists and is actually a shadow
  SELECT email INTO shadow_email
  FROM public.profiles
  WHERE user_id = shadow_user_id AND is_shadow = true;

  IF shadow_email IS NULL THEN
    RAISE EXCEPTION 'Shadow profile not found or is not a shadow profile';
  END IF;

  -- Step 2: Validate the real user exists
  SELECT email INTO real_email
  FROM public.profiles
  WHERE user_id = real_user_id AND is_shadow = false;

  IF real_email IS NULL THEN
    RAISE EXCEPTION 'Real user profile not found';
  END IF;

  -- Step 3: Verify email match (the real user's email must match the shadow's email)
  IF shadow_email <> real_email THEN
    RAISE EXCEPTION 'Email mismatch: shadow profile email does not match authenticated user email';
  END IF;

  -- Step 4: Transfer all references from shadow_user_id to real_user_id

  -- 4a: group_members
  UPDATE public.group_members
  SET user_id = real_user_id
  WHERE user_id = shadow_user_id;

  -- 4b: expenses (as creator)
  UPDATE public.expenses
  SET user_id = real_user_id
  WHERE user_id = shadow_user_id;

  -- 4c: expenses (as payer)
  UPDATE public.expenses
  SET paid_by = real_user_id
  WHERE paid_by = shadow_user_id;

  -- 4d: expense_splits
  UPDATE public.expense_splits
  SET user_id = real_user_id
  WHERE user_id = shadow_user_id;

  -- 4e: member_monthly_status
  UPDATE public.member_monthly_status
  SET user_id = real_user_id
  WHERE user_id = shadow_user_id;

  -- 4f: member_monthly_exclusions
  UPDATE public.member_monthly_exclusions
  SET user_id = real_user_id
  WHERE user_id = shadow_user_id;

  -- 4g: friends (update friend_id references)
  UPDATE public.friends
  SET friend_id = real_user_id
  WHERE friend_id = shadow_user_id;

  -- Step 5: Accept the friend request
  UPDATE public.friends
  SET status = 'accepted', updated_at = now()
  WHERE friend_id = real_user_id AND status = 'pending';

  -- Step 6: Delete the shadow profile
  DELETE FROM public.profiles
  WHERE user_id = shadow_user_id AND is_shadow = true;
END;
$$;
