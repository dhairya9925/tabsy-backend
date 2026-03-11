CREATE TABLE "contacts" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"owner_user_id" uuid NOT NULL,
	"linked_profile_id" uuid,
	"shadow_email" text,
	"shadow_name" text,
	"status" text DEFAULT 'shadow' NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "contacts_status_check" CHECK (status IN ('shadow', 'pending_link', 'linked', 'blocked')),
	CONSTRAINT "contacts_linked_or_shadow_check" CHECK ((linked_profile_id IS NOT NULL) OR (shadow_email IS NOT NULL))
);
--> statement-breakpoint
CREATE TABLE "friend_invites" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"inviter_user_id" uuid NOT NULL,
	"inviter_contact_id" uuid NOT NULL,
	"invitee_user_id" uuid,
	"invitee_email" text NOT NULL,
	"token" text NOT NULL,
	"status" text DEFAULT 'pending' NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	"accepted_at" timestamp with time zone,
	CONSTRAINT "friend_invites_token_key" UNIQUE("token"),
	CONSTRAINT "friend_invites_status_check" CHECK (status IN ('pending', 'accepted', 'declined', 'expired'))
);
--> statement-breakpoint
CREATE TABLE "shadow_profiles" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"owner_user_id" uuid NOT NULL,
	"contact_id" uuid NOT NULL,
	"display_name" text,
	"email" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
ALTER TABLE "contacts" ADD CONSTRAINT "contacts_linked_profile_id_fkey" FOREIGN KEY ("linked_profile_id") REFERENCES "public"."profiles"("id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "friend_invites" ADD CONSTRAINT "friend_invites_inviter_contact_id_fkey" FOREIGN KEY ("inviter_contact_id") REFERENCES "public"."contacts"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "shadow_profiles" ADD CONSTRAINT "shadow_profiles_contact_id_fkey" FOREIGN KEY ("contact_id") REFERENCES "public"."contacts"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE POLICY "Group members can update exclusions" ON "member_monthly_exclusions" AS PERMISSIVE FOR UPDATE TO "authenticated" USING (EXISTS (SELECT 1 FROM public.group_members gm WHERE gm.group_id = group_id AND gm.user_id = auth.uid()));--> statement-breakpoint
ALTER POLICY "Group members can view exclusions" ON "member_monthly_exclusions" TO authenticated USING (EXISTS (SELECT 1 FROM public.group_members gm WHERE gm.group_id = member_monthly_exclusions.group_id AND gm.user_id = auth.uid()));--> statement-breakpoint
ALTER POLICY "Group members can insert exclusions" ON "member_monthly_exclusions" TO authenticated WITH CHECK (EXISTS (SELECT 1 FROM public.group_members gm WHERE gm.group_id = group_id AND gm.user_id = auth.uid()));--> statement-breakpoint
ALTER POLICY "Group members can delete exclusions" ON "member_monthly_exclusions" TO authenticated USING (EXISTS (SELECT 1 FROM public.group_members gm WHERE gm.group_id = group_id AND gm.user_id = auth.uid()));