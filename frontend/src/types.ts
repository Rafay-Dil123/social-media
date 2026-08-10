export interface Profile {
  display_name: string;
  bio: string;
  avatar_url: string;
  created_at: string;
  updated_at: string;
}

export interface User {
  id: string;
  username: string;
  email: string;
  date_joined: string;
  profile: Profile | null;
}

/** Body returned by /auth/register and /auth/login. */
export interface AuthResponse {
  access_token: string;
  token_type: "Bearer";
  expires_in: number;
  user: User;
}

/** Body returned by /auth/refresh (no user). */
export interface RefreshResponse {
  access_token: string;
  token_type: "Bearer";
  expires_in: number;
}

/** Consistent API error envelope from the backend. */
export interface ApiError {
  error: {
    code: string;
    detail: string | Record<string, string[]>;
  };
}
