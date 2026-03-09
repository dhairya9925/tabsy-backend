import { serve } from "https://deno.land/std@0.168.0/http/server.ts"
import { Webhook } from "https://esm.sh/standardwebhooks@1.0.0"

const resendApiKey = Deno.env.get("RESEND_API_KEY")
const hookSecret = Deno.env.get("SEND_EMAIL_HOOK_SECRET")

serve(async (req) => {
  if (req.method !== "POST") {
    return new Response("not allowed", { status: 400 })
  }

  const payload = await req.text()
  const headers = Object.fromEntries(req.headers)
  const wh = new Webhook(hookSecret as string)
  
  try {
    const { user, email_data } = wh.verify(payload, headers) as {
      user: { email: string; user_metadata: { display_name?: string } }
      email_data: { token: string; token_hash: string; redirect_to: string; email_action_type: string }
    }
    
    // Default subject and content
    let subject = "Your Splitwise Buddy Account"
    let htmlContent = `<p>Click the link below to verify your account:</p>`
    
    // Depending on the action type from Supabase, customize the email content
    if (email_data.email_action_type === 'signup') {
        subject = "Welcome to Splitwise Buddy! Please verify your email."
        htmlContent = `
          <h2>Welcome, ${user.user_metadata?.display_name || 'Friend'}!</h2>
          <p>Thanks for signing up to Splitwise Buddy.</p>
          <p>Please confirm your email by clicking the link below:</p>
          <a href="${email_data.redirect_to}?token_hash=${email_data.token_hash}&type=signup">Confirm your email</a>
        `
    } else if (email_data.email_action_type === 'recovery') {
        subject = "Reset Your Password"
        htmlContent = `
          <h2>Password Reset Request</h2>
          <p>Please click the link below to reset your password:</p>
          <a href="${email_data.redirect_to}?token_hash=${email_data.token_hash}&type=recovery">Reset password</a>
        `
    } else if (email_data.email_action_type === 'magiclink') {
        subject = "Your Magic Link"
        htmlContent = `
          <h2>Your Magic Link</h2>
          <p>Click the link below to securely log in:</p>
          <a href="${email_data.redirect_to}?token_hash=${email_data.token_hash}&type=magiclink">Log in to Splitwise Buddy</a>
        `
    }

    const res = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${resendApiKey}`,
      },
      body: JSON.stringify({
        from: "Splitwise Buddy <onboarding@resend.dev>", // target domain when verified
        to: user.email,
        subject: subject,
        html: htmlContent,
      }),
    })

    const data = await res.json()

    if (res.ok) {
      return new Response(JSON.stringify(data), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    } else {
      console.error(data)
      return new Response(JSON.stringify({ error: data }), {
        status: 400,
        headers: { "Content-Type": "application/json" },
      })
    }
  } catch (error) {
    console.error(error)
    return new Response(JSON.stringify({ error: "Unauthorized" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    })
  }
})
